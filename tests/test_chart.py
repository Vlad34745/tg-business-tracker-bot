from core.chart import generate_category_chart


def test_generate_category_chart_returns_png_bytes():
    data = [("Кафе", 500.0), ("Таксі", 300.0), ("Продукти", 1200.0)]
    buffer = generate_category_chart(data, "Витрати за Липень 2026")
    assert buffer is not None
    content = buffer.read()
    assert len(content) > 0
    assert content[:8] == b"\x89PNG\r\n\x1a\n"  # PNG file signature


def test_generate_category_chart_empty_data_returns_none():
    assert generate_category_chart([], "Витрати") is None


def test_generate_category_chart_caps_at_top_n():
    data = [(f"Категорія{i}", float(i)) for i in range(20)]
    buffer = generate_category_chart(data, "Тест", top_n=5)
    assert buffer is not None
    assert len(buffer.read()) > 0