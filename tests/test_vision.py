from pathlib import Path

from PIL import Image, ImageDraw

from ghostos.perception.vision import annotate, compare


def _shot(path: Path, box: tuple[int, int, int, int] | None) -> Path:
    im = Image.new("RGB", (640, 400), "white")
    if box:
        ImageDraw.Draw(im).rectangle(box, fill="black")
    im.save(path)
    return path


def test_identical_screens_do_not_change(tmp_path: Path) -> None:
    a = _shot(tmp_path / "a.png", (10, 10, 60, 40))
    change = compare(a, a)
    assert not change.changed and change.region is None


def test_moved_element_is_localised(tmp_path: Path) -> None:
    a = _shot(tmp_path / "a.png", None)
    b = _shot(tmp_path / "b.png", (400, 300, 520, 360))
    change = compare(a, b)
    assert change.changed
    x, y, w, _h = change.region
    assert 390 <= x <= 410 and 290 <= y <= 310 and 100 <= w <= 140


def test_annotate_writes_image(tmp_path: Path) -> None:
    a = _shot(tmp_path / "a.png", None)
    out = annotate(
        a,
        tmp_path / "out.png",
        found=[10, 10, 50, 20],
        previous=[100, 100, 40, 20],
        caption="healed",
    )
    with Image.open(out) as im:
        assert im.size == (640, 400)
