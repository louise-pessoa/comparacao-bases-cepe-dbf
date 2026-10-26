"""As amostras reais da CEPE são versionadas: a ausência de qualquer uma reprova."""

from __future__ import annotations

import pytest

from conftest import AMOSTRAS, TIPO_ESPERADO_DECRETO


@pytest.mark.parametrize("numero", sorted(TIPO_ESPERADO_DECRETO))
def test_amostra_existe_e_e_um_pdf(numero):
    arquivo = AMOSTRAS / f"Decreto {numero}.pdf"
    assert arquivo.is_file(), f"amostra ausente: {arquivo}"
    assert arquivo.read_bytes()[:5] == b"%PDF-", f"não parece um PDF: {arquivo}"
