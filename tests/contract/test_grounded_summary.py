import pytest
from module_d.grounded_summary import build_evidence, validate_selection, summarize


def test_only_sourced_retrieved_description_is_included():
    e = build_evidence('分數17，份量未標示','未命中不代表不含添加物',[
        {'inDatabase':True,'name':'A','description':'說明','description_sources':['https://example.org']},
        {'inDatabase':True,'name':'B','description':'沒有出處'}])
    assert len(e)==3
    assert e[2]['sources']==['https://example.org']


@pytest.mark.parametrize('ids',[['0'],['0','2'],['0','1','99'],['0','1','1']])
def test_missing_caveats_unknown_and_duplicate_rejected(ids):
    with pytest.raises(ValueError):validate_selection({'selected_ids':ids},build_evidence('原分數','限制',[]))


def test_default_does_not_call_model(monkeypatch):
    monkeypatch.delenv('GEMINI_GROUNDED_SUMMARY_ENABLED',raising=False)
    assert summarize('原分數','原限制',[],client=object())['mode']=='rules'


def test_exact_original_text_retained():
    e=build_evidence('缺值不是0','未比對到不是不含添加物',[])
    assert validate_selection({'selected_ids':['1','0']},e)[1]['text']=='缺值不是0'


def test_enabled_output_and_citations(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv('GEMINI_GROUNDED_SUMMARY_ENABLED','1')
    def generate(**kwargs):
        assert kwargs['config'].response_mime_type=='application/json'
        return SimpleNamespace(text='{"selected_ids":["0","1","2"]}')
    client=SimpleNamespace(models=SimpleNamespace(generate_content=generate))
    result=summarize('17分，未標示纖維','只做標示判讀',[
        {'inDatabase':True,'name':'A','description':'原始說明','description_sources':['https://example.org']}],client)
    assert result['mode']=='gemini_extractive'
    assert result['overall']=='17分，未標示纖維'
    assert result['additives']=='只做標示判讀\nA：原始說明'
    assert result['citations'][-1]['sources']==['https://example.org']


def test_invalid_model_response_falls_back(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setenv('GEMINI_GROUNDED_SUMMARY_ENABLED','1')
    client=SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw:SimpleNamespace(text='{"selected_ids":["0","99"]}')))
    result=summarize('原分數','原限制',[],client)
    assert result['mode']=='rules'
    assert result['reason']=='ValueError'
    assert result['overall']=='原分數'
