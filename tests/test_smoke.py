"""Ticket 01: prove the package imports."""


def test_import() -> None:
    import pdf_router

    assert pdf_router.__version__
