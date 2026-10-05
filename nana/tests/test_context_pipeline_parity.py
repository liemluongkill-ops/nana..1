"""Offline Task 7 private planning contracts; no production owner imports."""
from dataclasses import fields, replace
import importlib
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT / 'tests/smoke'))
from smoke_context_runtime_integration import _install_private_dependencies, _run_stream


@pytest.fixture(autouse=True)
def _isolated_nana_modules():
    before = {
        name: module
        for name, module in sys.modules.items()
        if name == 'nana' or name.startswith('nana.')
    }
    yield
    for name in tuple(sys.modules):
        if (name == 'nana' or name.startswith('nana.')) and name not in before:
            sys.modules.pop(name, None)
    sys.modules.update(before)


def fixture(*, story=False, text='fixture query'):
    gpt, captures, reads = _install_private_dependencies(private_mode='legacy')
    sys.modules.pop('nana.runtime.memory_grounding', None)
    importlib.import_module('nana.runtime.memory_grounding')
    contracts = importlib.import_module('nana.runtime.context_contracts')
    runtime = importlib.import_module('nana.runtime.context_runtime')
    adapters = importlib.import_module('nana.runtime.context_adapters')
    compiler = importlib.import_module('nana.runtime.context_compiler')
    shadow = importlib.import_module('nana.runtime.context_shadow')
    boundary, turn = shadow.resolve_context_turn(
        text=text, viewer_name=None, stream_mode=False, public_platform=None,
        metadata=None, private_model='fake', public_model='fake-public',
        story_mode=story, casual_mode=False, temporal_intent=False,
        grounding_intent=False,
    )
    spec = (
        ('core.private.v1', contracts.Lifetime.STATIC, contracts.SemanticRole.INSTRUCTION, 'core', 'CORE'),
        ('policy.private.v1', contracts.Lifetime.STATIC, contracts.SemanticRole.INSTRUCTION, 'core', 'BOUNDARY'),
        ('contract.output.private.v1', contracts.Lifetime.STATIC, contracts.SemanticRole.INSTRUCTION, 'core', 'OUTPUT'),
        ('identity.owner.v1', contracts.Lifetime.DURABLE, contracts.SemanticRole.IDENTITY, 'owner_identity', 'OWNER'),
        ('expression.private.v1', contracts.Lifetime.TURN, contracts.SemanticRole.STATE, 'expression', 'TONE'),
        ('situation.current.v1', contracts.Lifetime.TURN, contracts.SemanticRole.STATE, 'runtime_state', 'STATE'),
    )
    sections = tuple(contracts.ContextSection(
        id=id, lifetime=lifetime, semantic_role=role, freshness=contracts.Freshness.FRESH,
        visibility=frozenset({contracts.Lane.PRIVATE_OWNER}),
        source=contracts.SourceRef(owner, 'fixture.v1', id), revision=owner+'-r1',
        authority=None, observed_at=turn.resolved_request.captured_wall_time,
        expires_at=None, conflict_key=None, dedupe_key=id, max_tokens=2500,
        payload=payload, formatter_version='fixture.v1', required=True,
        budget_class='mandatory', semantic_status='active', provenance=(), relevance=1.,
    ) for id,lifetime,role,owner,payload in spec)
    return gpt,captures,reads,contracts,runtime,adapters,compiler,turn.resolved_request,sections


def test_sync_and_stream_consume_one_frozen_plan_without_stream_dimension():
    _,_,_,c,r,a,_,request,sections = fixture()
    evidence=a.FrozenGroundingEvidence(status='found', confidence=.9,
        lane_visible_to='private_only', evidence_strength='strong', snippets=('GROUNDING',),
        has_legacy_timestamp=False, notes='', source_event_ids=('event-fixture',))
    inputs=r.GptTurnInput(request=request, sections=sections, evidence=evidence,
                          model='fake', max_output_tokens=200,
                          budget_revision=r.PRIVATE_POLICY_REVISION)
    assert not any('stream' in item.name for item in fields(inputs))
    first=r.build_turn_plan(inputs); second=r.build_turn_plan(inputs)
    assert first.compiled.messages==second.compiled.messages
    assert first.compiled.full_context_hash==second.compiled.full_context_hash
    assert tuple(m.role for m in first.compiled.messages)==('system','user')
    assert first.compiled.messages[0].content.count('MEMORY EVIDENCE:')==1
    assert 'GROUNDING' in first.compiled.messages[0].content
    assert first.evidence is not None
    assert first.compiled.manifest.budget_enforced is True
    assert next(s for s in first.compiled.manifest.sections if s.section_id=='memory.grounding.v1').included


def test_plan_rejects_wrong_revision_route_and_nonprivate_scope():
    _,_,_,c,r,_,_,request,sections=fixture()
    for revision in ('', 'approved', 'private-budget-review.2026-10-01.r1'):
        with pytest.raises(c.ContextContractError,match='budget_policy_unapproved'):
            r.build_turn_plan(r.GptTurnInput(request,sections,None,'fake',200,revision))
    shadow=importlib.import_module('nana.runtime.context_shadow')
    from smoke_context_runtime_integration import _strict_public_kwargs
    kw=_strict_public_kwargs()
    _,turn=shadow.resolve_context_turn(text='public query',private_model='fake',public_model='fake-public',
        story_mode=False,casual_mode=False,temporal_intent=False,grounding_intent=False,
        viewer_name=kw['viewer_name'],stream_mode=True,public_platform='youtube',metadata=kw['metadata'])
    with pytest.raises(c.ContextContractError,match='unapproved_canonical_profile'):
        r.build_turn_plan(r.GptTurnInput(turn.resolved_request,(),None,'fake-public',200,r.PRIVATE_POLICY_REVISION))


def test_missing_required_source_cannot_silently_vanish():
    _,_,_,c,r,_,_,request,sections=fixture()
    bad=tuple(replace(s,freshness=c.Freshness.UNKNOWN) if s.id=='situation.current.v1' else s for s in sections)
    with pytest.raises(c.ContextContractError,match='required_section_dropped'):
        r.build_turn_plan(r.GptTurnInput(request,bad,None,'fake',200,r.PRIVATE_POLICY_REVISION))


def test_private_minimum_identity_expression_and_grounding_remain_required():
    _,_,_,c,r,_,_,request,sections=fixture()
    for missing in ('identity.owner.v1','expression.private.v1'):
        with pytest.raises(c.ContextContractError,match='context_required_section_unavailable'):
            r.build_turn_plan(r.GptTurnInput(request,tuple(s for s in sections if s.id!=missing),None,'fake',200,r.PRIVATE_POLICY_REVISION))


def test_direct_plan_rejects_fast_route():
    _,_,_,c,r,_,_,request,sections=fixture()
    raw=c.UnresolvedContextRequest('fast-fixture','fast-correlation',c.Route.PRIVATE_FAST,
        'fake','fast text',None,False,None,{},False,False,False,False,False)
    class Authority:
        def authorize(self, _raw):
            return r.TrustedIngress(c.Lane.PRIVATE_OWNER,None)
    resolved=r.LaneResolver(Authority()).resolve(raw)
    with pytest.raises(c.ContextContractError,match='unapproved_canonical_profile'):
        r.build_turn_plan(r.GptTurnInput(resolved,sections,None,'fake',200,r.PRIVATE_POLICY_REVISION))


def test_real_gpt_canonical_sync_stream_share_builder_and_memory_bundle(monkeypatch):
    gpt,captures,reads,c,r,a,_,_,_=fixture()
    config=sys.modules['nana.config']
    monkeypatch.setattr(config,'NANA_CONTEXT_PRIVATE_MODE','canonical')
    monkeypatch.setattr(config,'NANA_CONTEXT_BUDGET_POLICY_REVISION',r.PRIVATE_POLICY_REVISION)
    # Real dispatch remains gated. This test patches that named readiness gate
    # only after all external source/transport dependencies are offline fixtures.
    monkeypatch.setattr(r,'require_canonical_dispatch_ready',lambda *_a,**_k: None)
    from types import SimpleNamespace
    monkeypatch.setattr(gpt,'_private_memory_evidence_for_turn',lambda *_a,**_k: (_ for _ in ()).throw(
        AssertionError('canonical path called parallel evidence retrieval')
    ))
    monkeypatch.setattr(gpt,'get_awareness_memory',lambda:SimpleNamespace(format_recent_moments_block=lambda **_k:'RECENT FIXTURE'))
    def reject(*_a,**_k):
        raise AssertionError('post-assembly injector called')
    monkeypatch.setattr(gpt.ConfidenceInjector,'inject_into_messages',reject)
    post_compile={'value':False}
    real_redact=gpt.redact_history_text
    def redaction_tripwire(text):
        assert not post_compile['value'], 'redaction after compile'
        return real_redact(text)
    monkeypatch.setattr(gpt,'redact_history_text',redaction_tripwire)
    def canonical_snapshot(context, awareness, request):
        captured_at=request.captured_wall_time
        def source(name, payload, *, fresh=False):
            return c.SourceSnapshot(
                source=name,revision=name+'-fixture-r1',
                observed_at=captured_at if fresh else None,
                captured_at=captured_at,
                freshness=c.Freshness.FRESH if fresh else c.Freshness.UNKNOWN,
                payload=payload,
            )
        runtime_source=source('runtime_context',{
            'active_app':context['active_app'],'active_zone':context['active_zone'],
            'idle_state':context['idle_state'],'in_flow':False,'time':{},
        })
        browser_source=source('browser_state',{'available':False})
        awareness_source=source('live_awareness',{
            'browser_available':False,'focus_source':'none','focus_text':'',
            'focus_confidence':0.0,
        })
        memory_source=source('awareness_memory',{'enabled':True,'moments':[]})
        checkpoint_source=source('private_checkpoint',{
            'available':False,'session_id':'','summary':'',
            'anchors':[],'pending_turns':[],
        })
        expression_source=source('expression_private',{
            'mood':{'tone':'steady','energy':0.5,'focus':0.5,'tension':0.18},
            'affect':{'warmth':0.6,'playfulness':0.5,'assertiveness':0.3,'intimacy':0.86},
            'persona':{'mode':'chill','clamp':'open'},
        },fresh=True)
        private_memory_source=source('private_memory',{'long_term':[]},fresh=True)
        sources=(runtime_source,browser_source,awareness_source,memory_source,checkpoint_source,expression_source,private_memory_source)
        return r.RuntimeContextSnapshot(
            request=request,snapshot_revision=1,captured_at=captured_at,
            captured_monotonic_at=request.captured_monotonic_time,
            runtime_context=runtime_source,browser_state=browser_source,
            live_awareness=awareness_source,awareness_memory=memory_source,
            private_checkpoint=checkpoint_source,
            expression=expression_source,
            private_memory=private_memory_source,
            source_revisions=tuple(a.SourceRevision(
                item.source,item.revision,
                'content_hash',
            ) for item in sorted(sources,key=lambda item:item.source)),
        )
    monkeypatch.setattr(gpt,'_canonical_current_snapshot',canonical_snapshot)
    seen=[]
    original=r.build_turn_plan
    def observe(inputs, **kwargs):
        plan=original(inputs, **kwargs); seen.append(plan);post_compile['value']=True; return plan
    monkeypatch.setattr(r,'build_turn_plan',observe)
    for story in (False,True):
        post_compile['value']=False
        assert gpt.ask_gpt('A full synthetic question',story_mode=story)
        post_compile['value']=False
        assert _run_stream(gpt,'A full synthetic question',story_mode=story)
    assert len(seen)==4
    for i in (0,2):
        assert seen[i].compiled.messages==seen[i+1].compiled.messages
        assert seen[i].compiled.full_context_hash==seen[i+1].compiled.full_context_hash
        assert seen[i].compiled.messages[0].content.count('MEMORY EVIDENCE:')==0
        assert seen[i].memory_bundle is not None
        assert seen[i].evidence is None
    assert len(captures['sync'])==len(captures['stream'])==2
    assert reads == {
        'context': 4,
        'awareness': 4,
        'retrieval': 0,
        'checkpoint': 0,
        'history': 0,
        'identity': 4,
    }


def test_production_canonical_readiness_remains_fail_closed(monkeypatch):
    gpt,captures,_,c,r,_,_,_,_=fixture()
    monkeypatch.setattr(sys.modules['nana.config'],'NANA_CONTEXT_PRIVATE_MODE','canonical')
    monkeypatch.setattr(sys.modules['nana.config'],'NANA_CONTEXT_BUDGET_POLICY_REVISION',r.PRIVATE_POLICY_REVISION)
    with pytest.raises(c.ContextContractError,match='canonical_runtime_not_ready'):
        gpt.ask_gpt('ordinary full question')
    assert captures=={'sync':[],'stream':[]}
