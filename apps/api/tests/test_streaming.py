import json
import pytest
import httpx
from httpx import AsyncClient, ASGITransport
from app.config import Settings
from app.main import create_app
from app.llm import stream_complete


def frame(body):
    return ('data: '+json.dumps(body,ensure_ascii=False)+'\n\n').encode()

class FragmentedStream(httpx.AsyncByteStream):
    def __init__(self, data):
        self.data = data
    async def __aiter__(self):
        # Split UTF-8 characters and SSE frames across network chunks.
        for index in range(0,len(self.data),3):
            yield self.data[index:index+3]


def mock_client(monkeypatch, data):
    real = httpx.AsyncClient
    def transport(request):
        payload = json.loads(request.content)
        assert payload['stream'] and payload['stream_options']['include_usage']
        return httpx.Response(200,headers={'content-type':'text/event-stream'},stream=FragmentedStream(data))
    monkeypatch.setattr('app.llm.httpx.AsyncClient',lambda **kw:real(transport=httpx.MockTransport(transport),**kw))
    return real


def upstream(complete=True):
    data = b': keepalive\n\n' + frame({'choices':[{'delta':{'reasoning_content':'private thinking'}}]})
    data += frame({'choices':[{'index':0,'delta':{'content':'你好'}}]})
    data += frame({'choices':[{'index':0,'delta':{'content':'，世界'}}]})
    if complete:
        data += frame({'choices':[{'delta':{},'finish_reason':'stop'}],'usage':{'total_tokens':21}}) + b'data: [DONE]\n\n'
    return data


def events(response):
    parsed=[]
    for block in response.text.strip().split('\n\n'):
        lines=block.splitlines()
        parsed.append((lines[0][7:],json.loads(lines[1][6:])))
    return parsed

async def test_live_stream_sse_persistence_and_replay(tmp_path,monkeypatch):
    real=mock_client(monkeypatch,upstream())
    settings=Settings(_env_file=None,data_dir=str(tmp_path),research_mode='live',llm_api_key='test',llm_model='deepseek-flash',llm_provider='deepseek')
    app=create_app(settings)
    async with app.router.lifespan_context(app):
        async with real(transport=ASGITransport(app=app),base_url='http://testserver') as client:
            await client.post('/api/auth/register',json={'username':'stream_admin','display_name':'test','password':'test-password-123'})
            chat=(await client.post('/api/conversations',json={})).json()
            url=f"/api/conversations/{chat['id']}/messages?stream=true"
            body={'content':'测试流式输出','client_id':'request-stream-1'}
            response=await client.post(url,json=body)
            assert response.status_code == 200 and response.headers['content-type'].startswith('text/event-stream')
            result=events(response)
            assert [e for e,_ in result] == ['start','delta','delta','done']
            assert ''.join(d['text'] for e,d in result if e=='delta') == '你好，世界'
            message=result[-1][1]['messages'][-1]
            assert message['content']=='你好，世界' and message['status']=='completed' and message['tokens']==21
            assert 'private thinking' not in response.text
            replay=events(await client.post(url,json=body))
            assert len(replay)==1 and replay[0][1]['replayed']
            assert len(replay[0][1]['messages'])==2

async def test_broken_stream_retains_partial_reply(tmp_path,monkeypatch):
    real=mock_client(monkeypatch,upstream(False))
    settings=Settings(_env_file=None,data_dir=str(tmp_path),research_mode='live',llm_api_key='test',llm_model='test')
    app=create_app(settings)
    async with app.router.lifespan_context(app):
        async with real(transport=ASGITransport(app=app),base_url='http://testserver') as client:
            await client.post('/api/auth/register',json={'username':'stream_admin','display_name':'test','password':'test-password-123'})
            chat=(await client.post('/api/conversations',json={})).json()
            result=events(await client.post(f"/api/conversations/{chat['id']}/messages?stream=true",json={'content':'测试','client_id':'request-failed-1'}))
            assert result[-1][0]=='error'
            message=result[-1][1]['messages'][-1]
            assert message['status']=='failed' and message['content'].startswith('你好，世界')
            saved=(await client.get(f"/api/conversations/{chat['id']}")).json()['messages'][-1]
            assert saved==message

async def test_demo_stream_without_external_calls(tmp_path):
    app=create_app(Settings(_env_file=None,data_dir=str(tmp_path),research_mode='demo'))
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app),base_url='http://testserver') as client:
            await client.post('/api/auth/register',json={'username':'stream_admin','display_name':'test','password':'test-password-123'})
            chat=(await client.post('/api/conversations',json={})).json()
            result=events(await client.post(f"/api/conversations/{chat['id']}/messages?stream=true",json={'content':'你好','client_id':'request-demo-1'}))
            assert len([1 for e,_ in result if e=='delta']) > 1
            assert result[-1][1]['messages'][-1]['status']=='completed'
            assert result[-1][1]['messages'][-1]['model']=='demo'
