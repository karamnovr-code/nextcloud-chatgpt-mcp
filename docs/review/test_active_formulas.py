"""No external execution: verify active formulas are rejected structurally."""
from io import BytesIO
import pytest
from openpyxl import Workbook
import doc_engine as engine


def test_active_formula_in_template_is_rejected():
    wb = Workbook()
    wb.active['A1'] = 'https://example.invalid/'
    wb.active['B1'] = '=WEBSERVICE(A1)'
    raw = BytesIO(); wb.save(raw)
    with pytest.raises(ValueError):
        engine.create_xlsx({'sheets': [{'name': 'Sheet', 'cells': {'C1': 'note'}}]}, raw.getvalue())


def test_image_formula_with_cell_reference_is_rejected():
    with pytest.raises(ValueError):
        engine.create_xlsx({'sheets': [{'rows': [['https://example.invalid/image.png', {'formula': '=IMAGE(A1)'}]]}]})
