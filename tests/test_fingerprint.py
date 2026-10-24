from ghostos.perception.fingerprint import Fingerprint, label_score, similarity, text_similarity
from ghostos.perception.locator import accept_fingerprint, site_key


def fp(**kw) -> Fingerprint:
    base = dict(
        selector="#x",
        tag="button",
        type="submit",
        text="Sign in",
        context="Sign in | form",
        order=2,
        x=0.5,
        y=0.6,
        classes=["btn", "btn-primary"],
    )
    base.update(kw)
    return Fingerprint(**base)


def test_text_similarity_basics() -> None:
    assert text_similarity("Save", "save") == 1.0
    assert text_similarity("Save", "Save invoice") >= 0.9
    assert text_similarity("Sign in", "Delete account") < 0.3
    assert text_similarity("", "x") == 0.0


def test_label_score_uses_every_name() -> None:
    field = Fingerprint(
        selector="#e", tag="input", label="Work email", placeholder="you@acme.io", name="username"
    )
    assert label_score("username", field) == 1.0
    assert label_score("work email", field) == 1.0


def test_redesigned_button_beats_distractors() -> None:
    ref = fp(selector="#login-btn", id="login-btn")
    redesigned = fp(
        selector='[data-testid="auth-submit"]',
        text="Log in",
        x=0.72,
        y=0.58,
        classes=["btn", "btn-primary", "btn-lg"],
        context="Welcome back | form",
    )
    forgot = Fingerprint(
        selector="a",
        tag="a",
        text="Forgot password?",
        order=3,
        x=0.72,
        y=0.66,
        context="Welcome back | form",
    )
    email = Fingerprint(
        selector="#user-email",
        tag="input",
        type="email",
        label="Work email",
        order=0,
        x=0.72,
        y=0.4,
        context="Work email | Welcome back | form",
    )
    scores = {c.selector: similarity(ref, c)[0] for c in (redesigned, forgot, email)}
    best = max(scores, key=scores.get)
    assert best == redesigned.selector
    runner_up = sorted(scores.values())[-2]
    assert accept_fingerprint(scores[best], runner_up)


def test_similarity_parts_are_bounded() -> None:
    score, parts = similarity(fp(), fp())
    assert 0.9 <= score <= 1.0
    assert all(0.0 <= v <= 1.0 for v in parts.values())


def test_ratio_test_rejects_ambiguous_matches() -> None:
    assert not accept_fingerprint(0.5, 0.47)
    assert accept_fingerprint(0.47, 0.31)
    assert accept_fingerprint(0.8, 0.7)
    assert not accept_fingerprint(0.3, 0.0)


def test_site_key_ignores_query_and_trailing_slash() -> None:
    assert site_key("http://h:1/app/?x=1#y") == "http://h:1/app"
    assert site_key("http://h:1") == "http://h:1/"
