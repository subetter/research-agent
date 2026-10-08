import pytest
from app.config import Settings
from app.providers import Provider, BudgetExceeded
from app.store import Store

async def test_report_reserved_and_cached_retry(tmp_path, monkeypatch):
    store = Store(tmp_path / 'budget.sqlite')
    project = store.create_project('预算测试', '')
    run = store.create_run(project['id'], '研究预算与报告', 'live', {})
    provider = Provider(Settings(_env_file=None, max_model_calls=5, report_reserved_calls=2), store)
    calls = []
    async def complete(*args, **kwargs):
        calls.append(1)
        return {'choices': [{'message': {'role': 'assistant', 'content': '{}'}}], 'usage': {'total_tokens': 10}}
    monkeypatch.setattr('app.providers.complete', complete)
    for index in range(3):
        await provider.chat(run['id'], [], f'research:{index}')
    with pytest.raises(BudgetExceeded):
        await provider.chat(run['id'], [], 'research:overflow')
    await provider.chat(run['id'], [], 'synthesize')
    await provider.chat(run['id'], [], 'synthesize')
    await provider.chat(run['id'], [], 'verify')
    with pytest.raises(BudgetExceeded):
        await provider.chat(run['id'], [], 'research:after-report')
    assert len(calls) == 5
    assert store.usage_count(run['id'], 'model') == 5
    store.close()

async def test_research_budget_degrades(tmp_path):
    store = Store(tmp_path / 'budget.sqlite')
    p = store.create_project('预算测试', '')
    run = store.create_run(p['id'], '测试研究降级', 'live', {})
    provider = Provider(Settings(_env_file=None), store)
    async def exhausted(*args, **kwargs):
        raise BudgetExceeded('研究预算已用尽')
    async def gate():
        pass
    provider.chat = exhausted
    result = await provider.live_research(run, {'subjects': ['A'], 'dimensions': ['能力'], 'max_search_calls': 2}, 'A', 0, gate)
    assert result['evidence_ids'] == []
    assert any(e['type'] == 'budget.reached' for e in store.query('SELECT * FROM events WHERE run_id=?', (run['id'],)))
    store.close()

async def test_failed_report_resumes_with_existing_evidence(tmp_path, monkeypatch):
    import asyncio
    from app.engine import Engine
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from langgraph.types import Command
    store = Store(tmp_path / 'resume.sqlite')
    settings = Settings(_env_file=None, data_dir=str(tmp_path), max_model_calls=5, report_reserved_calls=1)
    p = store.create_project('恢复测试', '')
    run = store.create_run(p['id'], '报告预算不足后的恢复', 'demo', {})
    async def complete(*args, **kwargs):
        return {'choices': [{'message': {'role': 'assistant', 'content': '{}'}}]}
    monkeypatch.setattr('app.providers.complete', complete)
    async def wait_for(status):
        async with asyncio.timeout(10):
            while store.run(run['id'])['status'] != status:
                await asyncio.sleep(.02)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / 'checkpoint.sqlite')) as checkpoint:
        engine = Engine(settings, store, checkpoint)
        original = engine.provider.synthesize
        async def synthesize(*args):
            await engine.provider.chat(run['id'], [], 'synthesize')
            return await original(*args)
        engine.provider.synthesize = synthesize
        engine.schedule(run['id'], {'run_id': run['id'], 'request': {'subjects': ['A'], 'dimensions': ['能力']}})
        await wait_for('waiting_input')
        for i in range(5):
            assert store.reserve(run['id'], f'old:{i}', 'model', 5)
            store.settle(run['id'], f'old:{i}')
        store.update(run['id'], status='running')
        engine.schedule(run['id'], Command(resume={'plan': store.run(run['id'])['plan']}))
        await wait_for('failed')
        ids = [e['id'] for e in store.evidence(run['id'])]
        assert ids
        assert '上限' in store.run(run['id'])['error']
        settings.max_model_calls = 8
        resume = await engine.resume_input(run['id'])
        store.update(run['id'], status='running', error=None)
        engine.schedule(run['id'], resume)
        await wait_for('completed')
        assert [e['id'] for e in store.evidence(run['id'])] == ids
        assert store.usage_count(run['id'], 'model') == 6
        await engine.shutdown()
    store.close()
