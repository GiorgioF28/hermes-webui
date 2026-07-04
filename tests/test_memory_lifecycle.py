"""Test della logica di decisione del ciclo memoria (Fase A, modulo puro)."""
from api import memory_lifecycle as ml


# --- context_pressure / should_compact ---

def test_context_pressure_ratio():
    assert ml.context_pressure(60_000, 100_000) == 0.6
    assert ml.context_pressure(0, 100_000) == 0.0


def test_context_pressure_clamped_and_safe():
    assert ml.context_pressure(200_000, 100_000) == 1.0  # clamp
    assert ml.context_pressure(50_000, 0) == 0.0          # finestra ignota -> 0
    assert ml.context_pressure(None, None) == 0.0         # dati assenti -> 0


def test_should_compact_threshold():
    assert ml.should_compact(0.60) is True   # soglia 60% raggiunta
    assert ml.should_compact(0.59) is False
    assert ml.should_compact(0.75) is True
    assert ml.should_compact(0.30, threshold=0.25) is True


# --- head_changed (rilevatore commit) ---

def test_head_changed():
    assert ml.head_changed("aaa111", "bbb222") is True
    assert ml.head_changed("aaa111", "aaa111") is False
    assert ml.head_changed(None, "aaa111") is True
    assert ml.head_changed("aaa111", "") is False   # curr ignoto -> non scattare


# --- is_new_topic (conservativo) ---

def test_new_topic_explicit_opener():
    assert ml.is_new_topic("ora occupiamoci del CRM multicanale") is True
    assert ml.is_new_topic("passiamo a un'altra cosa") is True
    assert ml.is_new_topic("cambiamo progetto, adesso la console") is True


def test_continuation_is_not_new_topic():
    # Il caso di Giorgio: A finita, ma B e C sono la prosecuzione dello stesso lavoro.
    assert ml.is_new_topic("ok abbiamo finito A, ora facciamo questi altri due sotto-task") is False
    assert ml.is_new_topic("continuiamo con il fix di prima") is False


def test_no_opener_is_not_new_topic():
    assert ml.is_new_topic("fammi vedere il codice") is False
    assert ml.is_new_topic("") is False
    assert ml.is_new_topic("grazie, perfetto") is False
