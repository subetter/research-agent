import json
import pytest
import httpx
from app.config import Settings
from app.llm import completion_payload, complete
from app.providers import Provider
from app.store import Store


def configuration(**overrides):
    return Settings(_env_file=None, llm_provider="deepseek", llm_base_url="https://api.deepseek.com",
                    llm_model="deepseek-flash", llm_api_key="test-key", **overrides)


def test_deepseek_payload_and_chat_auto_mode():
    settings = configuration()
    payload = completion_payload(settings, [{"role":"user","content":"hello"}])
    assert payload['model'] == 'deepseek-flash'
    assert payload['thinking'] == {'type':'disabled'} and 'reasoning_effort' not in payload
    assert payload['max_tokens'] == 8192 and payload['stream'] is False
    assert settings.effective_chat_mode == 'live' and settings.research_mode == 'demo'
    assert not settings.live_ready  # Web research still requires its search key.
    generic = Settings(_env_file=None, llm_provider='openai_compatible', llm_base_url='https://example.com/v1')
    assert 'thinking' not in completion_payload(generic, [])


async def test_deepseek_json_and_thinking_tool_history(tmp_path, monkeypatch):
    store = Store(tmp_path/'test.sqlite')
    project = store.create_project('test','')
    run = store.create_run(project['id'],'研究测试','live',{})
    provider = Provider(configuration(deepseek_thinking='enabled'), store)
    captured = []
    responses = iter([
        {'role':'assistant','content':'{"ok":true}'},
        {'role':'assistant','content':None,'reasoning_content':'test reasoning',
         'tool_calls':[{'id':'call1','type':'function','function':{'name':'search_project','arguments':'{"query":"test"}'}}]},
        {'role':'assistant','content':'调查完成','reasoning_content':'test final reasoning'},
    ])
    real_client = httpx.AsyncClient
    def model(request):
        assert str(request.url) == 'https://api.deepseek.com/chat/completions'
        assert request.headers['authorization'] == 'Bearer test-key'
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':next(responses),'finish_reason':'stop'}],'usage':{'total_tokens':11}})
    monkeypatch.setattr('app.llm.httpx.AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(model), **kw))
    first = await provider.chat(run['id'],[{'role':'system','content':'Output JSON'}], 'test-json')
    assert json.loads(first['content']) == {'ok':True}
    assert captured[0]['response_format'] == {'type':'json_object'}
    assert captured[0]['reasoning_effort'] == 'high'
    # Persisted operations must not create another charged request.
    await provider.chat(run['id'],[], 'test-json')
    assert len(captured) == 1
    async def gate():
        pass
    await provider.live_research(run,{'subjects':['A'],'dimensions':['功能'],'max_search_calls':2}, 'A',0,gate)
    assert 'tools' in captured[1] and 'response_format' not in captured[1]
    assert captured[2]['messages'][2]['reasoning_content'] == 'test reasoning'
    assert captured[2]['messages'][3]['role'] == 'tool'
    assert store.operation(run['id'],'research-model:A:0:0')['reasoning_content'] == 'test reasoning'
    store.close()


@pytest.mark.parametrize('status,expected',[(401,'Key'),(402,'余额'),(429,'频率'),(503,'繁忙')])
async def test_provider_error_does_not_leak_response_or_credentials(monkeypatch,status,expected):
    real_client = httpx.AsyncClient
    monkeypatch.setattr('app.llm.httpx.AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(lambda req:httpx.Response(status,json={'secret':'server-details'})),**kw))
    with pytest.raises(ValueError) as error:
        await complete(configuration(),[{'role':'user','content':'test'}])
    assert expected in str(error.value)
    assert 'server-details' not in str(error.value) and 'test-key' not in str(error.value)


@pytest.mark.parametrize('message,finish',[({'content':'','role':'assistant'},'stop'),({'content':'{"partial":','role':'assistant'},'length')])
async def test_empty_and_truncated_outputs_are_rejected(monkeypatch,message,finish):
    real_client = httpx.AsyncClient
    monkeypatch.setattr('app.llm.httpx.AsyncClient',lambda **kw:real_client(transport=httpx.MockTransport(lambda req:httpx.Response(200,json={'choices':[{'message':message,'finish_reason':finish}]})),**kw))
    with pytest.raises(ValueError):
        await complete(configuration(),[{'role':'user','content':'test'}],json_output=True)
