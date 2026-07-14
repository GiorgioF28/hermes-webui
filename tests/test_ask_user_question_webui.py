from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_frontend_renders_structured_ask_user_questions():
    src = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")

    assert "pending.questions" in src
    assert "_renderStructuredClarifyQuestions" in src
    assert "multiSelect" in src
    assert "clarify-other-input" in src
    assert "_collectStructuredClarifyResponse" in src


def test_clarify_styles_include_selected_state_and_descriptions():
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")

    assert ".clarify-choice.selected" in css
    assert ".clarify-option-description" in css
    assert ".clarify-question-block" in css

