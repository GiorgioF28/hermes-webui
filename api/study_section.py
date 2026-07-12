from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import unicodedata


ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_STUDY_ROOT = ROOT_DIR / "obsidian-vault" / "07-Study"
DEFAULT_COURSE = "Concorso INPS"
DEFAULT_STUDY_DIR = DEFAULT_STUDY_ROOT / DEFAULT_COURSE

INPS_SUBJECT_ALIASES: dict[str, tuple[str, ...]] = {
    "Informatica di base": ("informatica di base", "hardware", "software", "cpu", "ram", "memoria", "algoritmo", "bit", "byte"),
    "Linguaggi di programmazione": ("programmazione", "linguaggio", "python", "java", "javascript", "c++", "oop", "classe", "funzione", "variabile"),
    "Principi di Intelligenza Artificiale": ("intelligenza artificiale", " ai ", "machine learning", "deep learning", "rete neurale", "supervisionato", "chatgpt", "llm"),
    "Data privacy e sicurezza informatica": ("gdpr", "privacy", "sicurezza informatica", "cyber", "crittografia", "hash", "firewall", "data breach", "malware"),
    "Codice dell'Amministrazione Digitale (CAD)": ("cad", "codice amministrazione digitale", "spid", "pec", "firma digitale", "documento informatico", "conservazione"),
    "Nozioni di diritto amministrativo": ("diritto amministrativo", "procedimento amministrativo", "legge 241", "atto amministrativo", "accesso agli atti", "silenzio assenso"),
    "Sistemi operativi Linux e Windows": ("linux", "windows", "sistema operativo", "server", "client", "processo", "filesystem", "permessi", "bash", "powershell"),
    "Database relazionali": ("database", "sql", "relazionale", "tabella", "join", "chiave primaria", "chiave esterna", "normalizzazione", "query"),
    "Application server e middleware": ("application server", "middleware", "tomcat", "web server", "api gateway", "servlet", "runtime"),
    "Strumenti per l'office automation": ("office", "office automation", "excel", "word", "powerpoint", "libreoffice", "foglio di calcolo", "spreadsheet"),
    "Reti locali e geografiche": ("rete", "reti locali", "lan", "wan", "tcp", "ip", "subnet", "osi", "router", "switch", "dns"),
    "Reti multimediali (smart working)": ("smart working", "voip", "videoconferenza", "multimediale", "streaming", "webrtc", "qos"),
    "Backup e recovery": ("backup", "recovery", "restore", "disaster recovery", "rpo", "rto", "snapshot"),
    "Ordinamento del lavoro alle dipendenze delle PA": ("lavoro pa", "pubblica amministrazione", "dlgs 165", "dipendenze delle pa", "codice comportamento"),
    "Trasparenza, anticorruzione e privacy": ("trasparenza", "anticorruzione", "accesso civico", "anac", "whistleblowing", "foia"),
    "Lingua inglese": ("inglese", "english", "b1", "grammar", "reading", "listening", "vocabulary"),
}


def _empty_dashboard() -> dict[str, Any]:
    return {
        "quiz_links": [],
        "strategia": "",
        "calendario": [],
        "sessioni": [],
        "quiz_totali": 0,
    }


def _clean_markdown(value: str) -> str:
    value = re.sub(r"\[\[([^|\]]+\|)?([^\]]+)\]\]", r"\2", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"[*_`#>]", "", value)
    return re.sub(r"\s+", " ", value).strip(" -|\t")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_value.lower()).strip("-")


def _tag_slug(value: str) -> str:
    return _slug(str(value or "").replace("_", " ")).replace("-", "_") or "untagged"


def _relative_note_path(path: Path, base_dir: Path) -> str:
    try:
        return path.relative_to(ROOT_DIR).as_posix()
    except ValueError:
        try:
            return path.relative_to(base_dir).as_posix()
        except ValueError:
            return path.as_posix()


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    match = re.match(r"(?s)^---\s*\n(.*?)\n---\s*\n?(.*)$", text)
    if not match:
        return {}, text
    raw, body = match.group(1), match.group(2)
    data: dict[str, Any] = {}
    current_key = ""
    for line in raw.splitlines():
        if not line.strip():
            continue
        item = re.match(r"^\s*-\s+(.+?)\s*$", line)
        if item and current_key:
            data.setdefault(current_key, []).append(item.group(1).strip().strip("'\""))
            continue
        key_value = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if not key_value:
            continue
        current_key = key_value.group(1)
        value = key_value.group(2).strip()
        if value.startswith("[") and value.endswith("]"):
            data[current_key] = [
                part.strip().strip("'\"")
                for part in value.strip("[]").split(",")
                if part.strip()
            ]
        elif value:
            data[current_key] = value.strip("'\"")
        else:
            data[current_key] = []
    return data, body


def _safe_child_name(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", str(value or "")).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("invalid study name")
    return cleaned[:120]


def _course_dir(course: str | None = None, study_root: Path = DEFAULT_STUDY_ROOT) -> Path:
    root = Path(study_root)
    wanted = str(course or DEFAULT_COURSE).strip() or DEFAULT_COURSE
    for child in root.iterdir() if root.exists() else []:
        if child.is_dir() and child.name.lower() == wanted.lower():
            return child
    safe = _safe_child_name(wanted)
    return root / safe


def list_courses(study_root: Path = DEFAULT_STUDY_ROOT) -> list[dict[str, str]]:
    root = Path(study_root)
    courses = []
    if not root.exists():
        return courses
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        courses.append({
            "name": child.name,
            "source_dir": _relative_note_path(child, root),
            "has_index": (child / "Indice.md").exists(),
        })
    return courses


def _heading_key(title: str) -> str:
    cleaned = _clean_markdown(title).lower()
    return re.sub(r"[^a-z0-9àèéìòù]+", " ", cleaned).strip()


def split_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"": []}
    current = ""
    for line in text.splitlines():
        match = re.match(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", line)
        if match:
            current = _heading_key(match.group(1))
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _find_section(sections: dict[str, list[str]], *prefixes: str) -> list[str]:
    wanted = tuple(prefix.lower() for prefix in prefixes)
    for key, lines in sections.items():
        if key.startswith(wanted):
            return lines
    return []


def _parse_markdown_table(lines: list[str]) -> list[dict[str, str]]:
    table_lines = [line.strip() for line in lines if line.strip().startswith("|") and line.strip().endswith("|")]
    if len(table_lines) < 2:
        return []

    header = [cell.strip() for cell in table_lines[0].strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for raw in table_lines[2:]:
        if re.fullmatch(r"\|?\s*[-:| ]+\s*\|?", raw):
            continue
        cells = [cell.strip() for cell in raw.strip("|").split("|")]
        if len(cells) < len(header):
            cells.extend([""] * (len(header) - len(cells)))
        row = {header[index]: cells[index] if index < len(cells) else "" for index in range(len(header))}
        if any(_clean_markdown(value) and _clean_markdown(value) != "-" for value in row.values()):
            rows.append(row)
    return rows


def _value_by_header(row: dict[str, str], *needles: str) -> str:
    for header, value in row.items():
        normalized = _heading_key(header)
        if any(needle in normalized for needle in needles):
            return _clean_markdown(value)
    return ""


def _parse_links(lines: list[str]) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    seen: set[str] = set()
    for line in lines:
        source_label = ""
        source_match = re.search(r"\*\*([^*]+)\*\*", line)
        if source_match:
            source_label = _clean_markdown(source_match.group(1))
        for match in re.finditer(r"\[([^\]]+)\]\((https?://[^)]+)\)", line):
            url = match.group(2).strip()
            if url in seen:
                continue
            seen.add(url)
            label = _clean_markdown(" - ".join(part for part in [source_label, match.group(1)] if part))
            links.append({"label": label or url, "url": url})
    return links


def parse_dashboard(index_path: Path) -> dict[str, Any]:
    dashboard = _empty_dashboard()
    text = _read_text(index_path)
    if not text:
        return dashboard

    sections = split_sections(text)
    quiz_lines = _find_section(sections, "quiz")
    dashboard["quiz_links"] = _parse_links(quiz_lines)

    for lines in (quiz_lines, _find_section(sections, "penalita")):
        for line in lines:
            if "strategia" in line.lower() and _clean_markdown(line):
                dashboard["strategia"] = _clean_markdown(line)
                break
        if dashboard["strategia"]:
            break

    calendario = _find_section(sections, "calendario", "piano 2h")
    calendar_rows = _parse_markdown_table(calendario)
    if calendar_rows:
        dashboard["calendario"] = [
            " - ".join(
                value
                for value in [
                    _value_by_header(row, "slot"),
                    _value_by_header(row, "durata"),
                    _value_by_header(row, "cosa fare"),
                ]
                if value
            )
            for row in calendar_rows
        ]
    else:
        dashboard["calendario"] = [
            _clean_markdown(line)
            for line in calendario
            if _clean_markdown(line) and not re.fullmatch(r"[-: |]+", line.strip("| "))
        ][:12]

    session_rows = _parse_markdown_table(_find_section(sections, "log sessioni"))
    dashboard["sessioni"] = [
        {
            "data": _value_by_header(row, "data"),
            "ore": _value_by_header(row, "ore"),
            "materia": _value_by_header(row, "materia"),
        }
        for row in session_rows
        if _value_by_header(row, "data") and _value_by_header(row, "data") != "-"
    ]

    total = 0
    for row in _parse_markdown_table(_find_section(sections, "contatore quiz")):
        value = _value_by_header(row, "totale quiz")
        if value.isdigit():
            total += int(value)
    dashboard["quiz_totali"] = total
    return dashboard


def _parse_argomenti(lines: list[str]) -> dict[str, Any]:
    items = []
    for line in lines:
        match = re.match(r"^\s*[-*]\s+\[([ xX])\]\s+(.+)$", line)
        if match:
            items.append({"text": _clean_markdown(match.group(2)), "done": match.group(1).lower() == "x"})
    return {
        "done": sum(1 for item in items if item["done"]),
        "total": len(items),
        "items": items,
    }


def _parse_imparate(lines: list[str]) -> list[dict[str, str]]:
    table = _parse_markdown_table(lines)
    if table:
        parsed = []
        for row in table:
            titolo = _value_by_header(row, "argomento", "titolo") or _value_by_header(row, "data")
            testo = _value_by_header(row, "riassunto", "testo", "spiegazione")
            if titolo != "-" and testo != "-":
                parsed.append({"titolo": titolo, "testo": testo})
        return parsed

    parsed = []
    for line in lines:
        match = re.match(r"^\s*[-*]\s+(.+)$", line)
        if match and _clean_markdown(match.group(1)):
            parsed.append({"titolo": _clean_markdown(match.group(1)), "testo": ""})
    return parsed


def _parse_quiz(lines: list[str]) -> list[dict[str, str]]:
    return [
        {
            "data": _value_by_header(row, "data"),
            "fonte": _value_by_header(row, "fonte"),
            "punteggio": _value_by_header(row, "punteggio", "score"),
            "errori": _value_by_header(row, "errori", "rivedere"),
        }
        for row in _parse_markdown_table(lines)
        if _value_by_header(row, "data") != "-"
    ]


def _parse_da_ripassare(lines: list[str]) -> list[str]:
    values = []
    for line in lines:
        match = re.match(r"^\s*[-*]\s+(.*)$", line)
        if match:
            value = _clean_markdown(match.group(1))
            if value and value != "-":
                values.append(value)
    return values


def parse_subject(path: Path, base_dir: Path) -> dict[str, Any]:
    name = path.stem
    note_path = _relative_note_path(path, base_dir)
    try:
        text = _read_text(path)
        frontmatter, body = _parse_frontmatter(text)
        text_for_sections = body or text
        sections = split_sections(text_for_sections)
        file_stat = path.stat()
        chapters = parse_subject_chapters(base_dir, name)
        return {
            "name": name,
            "note_path": note_path,
            "tags": frontmatter.get("tags", []) if isinstance(frontmatter.get("tags"), list) else [],
            "argomenti": _parse_argomenti(_find_section(sections, "argomenti")),
            "imparate": _parse_imparate(_find_section(sections, "cose imparate")),
            "quiz": _parse_quiz(_find_section(sections, "quiz")),
            "da_ripassare": _parse_da_ripassare(_find_section(sections, "da ripassare")),
            "chapters": chapters,
            "last_updated": datetime.fromtimestamp(file_stat.st_mtime, tz=timezone.utc).isoformat(),
            "parse_ok": bool(text),
        }
    except Exception as error:
        return {
            "name": name,
            "note_path": note_path,
            "argomenti": {"done": 0, "total": 0, "items": []},
            "imparate": [],
            "quiz": [],
            "da_ripassare": [],
            "chapters": [],
            "last_updated": "",
            "parse_ok": False,
            "parse_error": str(error),
        }


def parse_chapter(path: Path, base_dir: Path, subject_name: str) -> dict[str, Any]:
    text = _read_text(path)
    frontmatter, body = _parse_frontmatter(text)
    sections = split_sections(body or text)
    name = str(frontmatter.get("chapter") or path.stem).strip() or path.stem
    subject_slug = _tag_slug(str(frontmatter.get("subject") or subject_name))
    chapter_slug = _tag_slug(str(frontmatter.get("chapter_slug") or name))
    default_tag = f"{subject_slug}/{chapter_slug}"
    raw_tags = frontmatter.get("tags", [])
    tags = [str(tag).strip().lower() for tag in raw_tags if str(tag).strip()] if isinstance(raw_tags, list) else []
    if default_tag not in tags:
        tags.insert(0, default_tag)
    try:
        file_stat = path.stat()
        last_updated = datetime.fromtimestamp(file_stat.st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        last_updated = ""
    ripassi = _parse_da_ripassare(_find_section(sections, "da ripassare", "ripasso"))
    spiegazioni = _find_section(sections, "spiegazioni")
    riassunto = "\n".join(line.strip() for line in _find_section(sections, "riassunto") if line.strip()).strip()
    return {
        "name": name,
        "subject": subject_name,
        "slug": chapter_slug,
        "tag": default_tag,
        "tags": tags,
        "status": str(frontmatter.get("status") or "da iniziare"),
        "note_path": _relative_note_path(path, base_dir),
        "da_ripassare": ripassi,
        "summary": _clean_markdown(riassunto)[:320],
        "spiegazioni_count": sum(1 for line in spiegazioni if re.match(r"^\s{0,3}###\s+", line)),
        "last_updated": last_updated,
        "parse_ok": bool(text),
    }


def parse_subject_chapters(base_dir: Path, subject_name: str) -> list[dict[str, Any]]:
    chapter_dir = Path(base_dir) / subject_name
    if not chapter_dir.exists() or not chapter_dir.is_dir():
        return []
    chapters = [
        parse_chapter(path, base_dir, subject_name)
        for path in sorted(chapter_dir.glob("*.md"), key=lambda p: p.stem.lower())
        if path.name.lower() != "index.md"
    ]
    return chapters


def _subject_order(index_path: Path) -> list[str]:
    names = []
    for row in _parse_markdown_table(_find_section(split_sections(_read_text(index_path)), "materie e copertura")):
        materia = _value_by_header(row, "materia")
        if materia and materia != "-":
            names.append(materia)
    return names


def build_summary(study_dir: Path = DEFAULT_STUDY_DIR, *, course: str | None = None) -> dict[str, Any]:
    try:
        study_dir = _course_dir(course) if course else Path(study_dir)
        index_path = study_dir / "Indice.md"
        subjects = [
            path for path in study_dir.glob("*.md")
            if path.name.lower() not in {"indice.md", "error log.md"}
        ]
        order = {name.lower(): index for index, name in enumerate(_subject_order(index_path))}
        subjects.sort(key=lambda path: (order.get(path.stem.lower(), 999), path.stem.lower()))
        try:
            source_dir = study_dir.relative_to(ROOT_DIR).as_posix()
        except ValueError:
            source_dir = study_dir.as_posix()
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "course": study_dir.name,
            "source_dir": source_dir,
            "courses": list_courses(study_dir.parent),
            "dashboard": parse_dashboard(index_path),
            "subjects": [parse_subject(path, study_dir) for path in subjects],
        }
    except Exception as error:
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "course": str(course or Path(study_dir).name),
            "source_dir": str(study_dir),
            "courses": [],
            "dashboard": _empty_dashboard(),
            "subjects": [],
            "parse_ok": False,
            "parse_error": str(error),
        }


def find_chapter_by_tag(course: str | None, tag: str, study_root: Path = DEFAULT_STUDY_ROOT) -> tuple[Path, dict[str, Any], str] | None:
    course_dir = _course_dir(course, study_root)
    wanted = str(tag or "").strip().lower()
    if not wanted:
        return None
    for subject_file in course_dir.glob("*.md"):
        if subject_file.name.lower() in {"indice.md", "error log.md"}:
            continue
        subject = subject_file.stem
        chapter_dir = course_dir / subject
        if not chapter_dir.is_dir():
            continue
        for chapter_path in chapter_dir.glob("*.md"):
            chapter = parse_chapter(chapter_path, course_dir, subject)
            if wanted in {str(t).lower() for t in chapter.get("tags", [])}:
                return chapter_path, chapter, subject
    return None


def _error_log_matches(course_dir: Path, tag: str, *, limit: int = 12) -> list[dict[str, str]]:
    matches: list[dict[str, str]] = []
    wanted = tag.lower()
    paths = [course_dir / "Error log.md", *course_dir.glob("*/*.md")]
    for path in paths:
        text = _read_text(path)
        if not text:
            continue
        for line in reversed(text.splitlines()):
            if wanted not in line.lower():
                continue
            cleaned = _clean_markdown(line)
            if cleaned:
                matches.append({"source": _relative_note_path(path, course_dir), "text": cleaned[:500]})
            if len(matches) >= limit:
                return matches
    return matches


def retrieve_by_tag(course: str | None, tag: str, study_root: Path = DEFAULT_STUDY_ROOT) -> dict[str, Any]:
    course_dir = _course_dir(course, study_root)
    requested_tag = str(tag or "").strip().lower()
    found = find_chapter_by_tag(course_dir.name, tag, study_root)
    if not found:
        return {
            "course": course_dir.name,
            "tag": requested_tag,
            "found": False,
            "chapter": None,
            "page_markdown": "",
            "recent_errors": _error_log_matches(course_dir, requested_tag),
        }
    chapter_path, chapter, subject = found
    chapter_tag = chapter.get("tag") or str(tag or "").strip().lower()
    recent_errors = _error_log_matches(course_dir, requested_tag)
    if requested_tag != chapter_tag:
        seen = {row.get("text") for row in recent_errors}
        for row in _error_log_matches(course_dir, chapter_tag):
            if row.get("text") not in seen:
                recent_errors.append(row)
    return {
        "course": course_dir.name,
        "subject": subject,
        "tag": chapter_tag,
        "found": True,
        "chapter": chapter,
        "page_markdown": _read_text(chapter_path),
        "recent_errors": recent_errors,
    }


def _message_words(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9àèéìòù]+", str(value or "").lower())
        if len(token) > 2
    }


def _best_subject_from_message(message: str, course: str | None = None, study_root: Path = DEFAULT_STUDY_ROOT) -> str:
    text = f" {str(message or '').lower()} "
    words = _message_words(text)
    scores: dict[str, int] = {}
    canonical_scores: dict[str, int] = {}
    for subject, aliases in INPS_SUBJECT_ALIASES.items():
        score = 0
        for alias in aliases:
            alias_l = alias.lower()
            if alias_l.strip() in text:
                score += max(3, len(alias_l.split()) * 3)
            else:
                score += len(words.intersection(_message_words(alias_l)))
        if score:
            canonical_scores[subject] = score
            scores[subject] = score

    if canonical_scores:
        return sorted(canonical_scores.items(), key=lambda item: (-item[1], item[0].lower()))[0][0]

    try:
        summary = build_summary(_course_dir(course, study_root))
        for subject in summary.get("subjects") or []:
            name = str(subject.get("name") or "")
            if not name:
                continue
            score = scores.get(name, 0)
            name_words = _message_words(name)
            if name.lower() in text:
                score += 10
            score += len(words.intersection(name_words)) * 2
            for chapter in subject.get("chapters") or []:
                chapter_words = _message_words(chapter.get("name") or "")
                overlap = len(words.intersection(chapter_words))
                if overlap:
                    score += overlap
            if score:
                scores[name] = score
    except Exception:
        pass

    if not scores:
        return "Informatica di base"
    return sorted(scores.items(), key=lambda item: (-item[1], item[0].lower()))[0][0]


def _candidate_chapter_from_message(
    message: str,
    subject: str,
    course: str | None = None,
    study_root: Path = DEFAULT_STUDY_ROOT,
) -> str:
    text = str(message or "").strip()
    text_l = text.lower()
    words = _message_words(text_l)
    try:
        summary = build_summary(_course_dir(course, study_root))
        for row in summary.get("subjects") or []:
            if str(row.get("name") or "").lower() != subject.lower():
                continue
            best: tuple[int, str] = (0, "")
            for chapter in row.get("chapters") or []:
                name = str(chapter.get("name") or "")
                if not name:
                    continue
                score = 12 if name.lower() in text_l else len(words.intersection(_message_words(name))) * 3
                tag = str(chapter.get("tag") or "")
                score += len(words.intersection(_message_words(tag.replace("/", " "))))
                if score > best[0]:
                    best = (score, name)
            if best[0] >= 3:
                return best[1]
    except Exception:
        pass

    topic_patterns = [
        r"\b(?:su|sul|sulla|sulle|riguardo|circa|argomento|capitolo)\s+([A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9 '\-_/]{2,48})",
        r"\b(?:cos[' ]?e|cosa sono|spiegami|riassumi|ripassiamo)\s+([A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9 '\-_/]{2,48})",
    ]
    for pattern in topic_patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            value = _clean_markdown(match.group(1)).strip(" ?.:;,-")
            if value:
                return value[:80]

    for alias in INPS_SUBJECT_ALIASES.get(subject, ()):
        alias_clean = alias.strip()
        if len(alias_clean) >= 3 and alias_clean.lower() in text_l:
            return alias_clean[:80].title() if alias_clean.islower() else alias_clean[:80]

    if "?" in text:
        before = _clean_markdown(text.split("?", 1)[0])
        if before:
            return before[:72].strip(" .,:;-") or "Domande ed errori"
    return "Conversazione Professore"


def classify_study_message(
    message: str,
    *,
    course: str | None = None,
    study_root: Path = DEFAULT_STUDY_ROOT,
) -> dict[str, Any]:
    subject = _best_subject_from_message(message, course=course, study_root=study_root)
    chapter = _candidate_chapter_from_message(message, subject, course=course, study_root=study_root)
    tag = f"{_tag_slug(subject)}/{_tag_slug(chapter)}"
    text = str(message or "").strip()
    lowered = text.lower()
    is_question = "?" in text or bool(re.match(r"^\s*(come|cosa|cos[' ]?e|perche|perché|quando|quale|quali|a cosa)\b", lowered))
    is_booklet = len(text) >= 700 or any(token in lowered for token in ("libricino", "manuale", "pagina", "estratto", "paragrafo", "capitolo del libro"))
    mode = "libricino" if is_booklet else ("quiz" if is_question else "nota")
    return {
        "course": str(course or DEFAULT_COURSE).strip() or DEFAULT_COURSE,
        "subject": subject,
        "chapter": chapter,
        "tag": tag,
        "mode": mode,
        "is_question": is_question,
        "is_booklet": is_booklet,
    }


def autosave_professor_turn(
    *,
    course: str | None,
    message: str,
    study_root: Path = DEFAULT_STUDY_ROOT,
) -> dict[str, Any]:
    classification = classify_study_message(message, course=course, study_root=study_root)
    mode = classification["mode"]
    clean = str(message or "").strip()
    if mode == "libricino":
        summary = "Estratto/manuale incollato in chat Professore per ristudio."
        explanation = clean
        quiz = None
    elif classification["is_question"]:
        summary = "Domanda emersa nella chat Professore."
        explanation = clean
        quiz = {"question": clean[:500], "source": "Chat Professore"}
    else:
        summary = clean[:1200]
        explanation = clean
        quiz = None
    saved = save_chapter_memory(
        course=classification["course"],
        subject=classification["subject"],
        chapter=classification["chapter"],
        summary=summary,
        explanation=explanation,
        quiz=quiz,
        study_root=study_root,
    )
    return {**classification, "saved": saved}


def _chapter_template(course: str, subject: str, chapter: str, tag: str) -> str:
    return f"""---
course: {course}
subject: {subject}
chapter: {chapter}
chapter_slug: {_tag_slug(chapter)}
status: da iniziare
tags: [{tag}, type/study-chapter, project/concorso-inps-assistente-informatico]
---

# {chapter}

Materia: [[../{subject}|{subject}]]

## Riassunto

-

## Spiegazioni

-

## Quiz ed errori

| Data | Fonte | Domanda | Risposta corretta | Errore | Tag |
|---|---|---|---|---|---|

## Da ripassare

-
"""


def _append_under_heading(text: str, heading: str, block: str) -> str:
    heading_re = re.compile(rf"(?m)^##\s+{re.escape(heading)}\s*$")
    match = heading_re.search(text)
    if not match:
        return text.rstrip() + f"\n\n## {heading}\n\n{block.strip()}\n"
    next_match = re.search(r"(?m)^##\s+", text[match.end():])
    insert_at = len(text) if not next_match else match.end() + next_match.start()
    before = text[:insert_at].rstrip()
    after = text[insert_at:]
    return before + "\n\n" + block.strip() + "\n" + after


def save_chapter_memory(
    *,
    course: str | None,
    subject: str,
    chapter: str,
    explanation: str = "",
    summary: str = "",
    quiz: dict[str, Any] | None = None,
    study_root: Path = DEFAULT_STUDY_ROOT,
) -> dict[str, Any]:
    course_dir = _course_dir(course, study_root)
    subject_name = _safe_child_name(subject)
    chapter_name = _safe_child_name(chapter)
    subject_slug = _tag_slug(subject_name)
    chapter_slug = _tag_slug(chapter_name)
    tag = f"{subject_slug}/{chapter_slug}"
    chapter_dir = course_dir / subject_name
    chapter_dir.mkdir(parents=True, exist_ok=True)
    chapter_path = chapter_dir / f"{chapter_name}.md"
    if not chapter_path.exists():
        chapter_path.write_text(_chapter_template(course_dir.name, subject_name, chapter_name, tag), encoding="utf-8")

    text = _read_text(chapter_path)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    if summary.strip():
        text = _append_under_heading(text, "Riassunto", f"### Aggiornamento {now}\n\n{summary.strip()}")
    if explanation.strip():
        text = _append_under_heading(text, "Spiegazioni", f"### Spiegazione {now}\n\n{explanation.strip()}")

    quiz = quiz or {}
    question = str(quiz.get("question") or quiz.get("domanda") or "").replace("|", "/").strip()
    if question:
        source = str(quiz.get("source") or quiz.get("fonte") or "chat Professore").replace("|", "/").strip()
        correct = str(quiz.get("correct") or quiz.get("risposta_corretta") or "").replace("|", "/").strip()
        error = str(quiz.get("error") or quiz.get("errore") or "").replace("|", "/").strip()
        row = f"| {now[:10]} | {source} | {question} | {correct or '-'} | {error or '-'} | {tag} |"
        text = _append_under_heading(text, "Quiz ed errori", row)
        error_log = course_dir / "Error log.md"
        if not error_log.exists():
            error_log.write_text(
                "# Error log\n\n| Data | Materia | Capitolo | Fonte | Domanda | Risposta corretta | Errore | Tag |\n|---|---|---|---|---|---|---|---|\n",
                encoding="utf-8",
            )
        log_text = _read_text(error_log).rstrip()
        log_text += f"\n| {now[:10]} | {subject_name} | {chapter_name} | {source} | {question} | {correct or '-'} | {error or '-'} | {tag} |\n"
        error_log.write_text(log_text, encoding="utf-8")

    chapter_path.write_text(text.rstrip() + "\n", encoding="utf-8")
    return {
        "ok": True,
        "course": course_dir.name,
        "subject": subject_name,
        "chapter": chapter_name,
        "tag": tag,
        "note_path": _relative_note_path(chapter_path, course_dir),
    }


def build_professor_context(course: str | None, tag: str | None = None) -> str:
    summary = build_summary(course=course)
    dashboard = summary.get("dashboard") or {}
    subjects = summary.get("subjects") or []
    active = retrieve_by_tag(summary.get("course"), tag) if tag else {}
    subject_lines = []
    for subject in subjects[:20]:
        chapters = subject.get("chapters") or []
        subject_lines.append(
            f"- {subject.get('name')}: {len(chapters)} capitoli, "
            f"ripasso aperto {len(subject.get('da_ripassare') or [])}"
        )
    return "\n".join([
        "Sei il Professore di Hermes per lo studio del concorso INPS assistente informatico.",
        "Parla in italiano, spiega come un tutor, interroga, correggi, e resta nel contesto studio.",
        "Il backend classifica e salva automaticamente ogni turno nel DB studio: non chiedere a Giorgio di scegliere materia, capitolo o tag.",
        "Se Giorgio incolla estratti dal manuale o pagine del libricino, trattali come materiale da ristudio e produci spiegazione, punti deboli e domande di controllo.",
        f"Corso attivo: {summary.get('course')}",
        f"Strategia: {dashboard.get('strategia') or '-'}",
        "Materie:",
        *subject_lines,
        f"Tag/capitolo attivo: {tag or '-'}",
        "Contesto capitolo:",
        (active.get("page_markdown") or "-")[:6000] if active else "-",
        "Errori recenti:",
        "\n".join(f"- {row.get('text')}" for row in (active.get("recent_errors") or [])[:8]) if active else "-",
    ])


def main() -> int:
    study_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_STUDY_DIR
    print(json.dumps(build_summary(study_dir), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
