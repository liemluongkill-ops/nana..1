"""Task 7 immutable evidence, redaction and true-manifest audit regressions."""
from dataclasses import FrozenInstanceError, replace
import importlib

import pytest

from test_context_pipeline_parity import fixture


def test_frozen_evidence_does_not_alias_source_or_verifier():
    _,_,_,_,r,a,_,request,sections=fixture()
    from types import SimpleNamespace
    raw=SimpleNamespace(status='found',confidence=.9,lane_visible_to='private_only',
        evidence_strength='strong',snippets=['secret'],has_legacy_timestamp=False,
        notes='',source_event_ids=['event'])
    evidence=a.freeze_grounding_evidence(raw,redact=lambda s:s.replace('secret','masked'))
    raw.snippets.append('late'); raw.source_event_ids.append('late-id')
    plan=r.build_turn_plan(r.GptTurnInput(request,sections,evidence,'fake',200,r.PRIVATE_POLICY_REVISION))
    assert plan.evidence.snippets==('masked',)
    assert 'late' not in plan.compiled.messages[0].content
    with pytest.raises((FrozenInstanceError,AttributeError,TypeError)):
        plan.compiled.messages[0].content='mutated'
    with pytest.raises((FrozenInstanceError,AttributeError,TypeError)):
        plan.evidence.snippets+=('new',)
    verifier=plan.evidence.to_verifier_evidence()
    verifier.snippets.append('local-only')
    assert plan.evidence.snippets==('masked',)


def test_redaction_happens_before_compile_only(monkeypatch):
    _,_,_,_,r,_,compiler,request,sections=fixture(text='raw secret')
    phases=[]
    def redact(value):
        assert 'compiled' not in phases
        phases.append('redact')
        return value.replace('secret','masked')
    original=compiler.ContextCompiler.compile
    def compile_once(self,*args,**kwargs):
        result=original(self,*args,**kwargs);phases.append('compiled');return result
    monkeypatch.setattr(compiler.ContextCompiler,'compile',compile_once)
    sections=tuple(replace(s,payload='core secret') if s.id=='core.private.v1' else s for s in sections)
    plan=r.build_turn_plan(r.GptTurnInput(request,sections,None,'fake',200,r.PRIVATE_POLICY_REVISION),redact=redact)
    assert phases[-1]=='compiled'
    assert 'secret' not in repr(plan.compiled.messages)
    assert plan.compiled.messages[1].content=='raw masked'


def test_budget_audit_uses_actual_manifest_without_source_reads():
    _,_,_,_,r,_,_,request,sections=fixture()
    plan=r.build_turn_plan(r.GptTurnInput(request,sections,None,'fake',200,r.PRIVATE_POLICY_REVISION))
    audit=importlib.import_module('nana.runtime.context_budget_audit')
    report=audit.build_context_budget_audit('private_owner',compiled=plan.compiled)
    assert report.total_chars==plan.compiled.manifest.input_chars
    assert report.total_tokens_est==plan.compiled.manifest.input_tokens_est
    assert 'CORE' not in repr(report.to_dict())
    assert report.to_dict()['measurement']=='compiled_manifest'


def test_source_owned_evidence_enums_and_mutable_input_are_preserved():
    _,_,_,_,r,a,_,request,sections=fixture()
    grounding=importlib.import_module('nana.runtime.memory_grounding')
    for strength in ('weak','moderate','strong'):
        raw=grounding.MemoryEvidence('partial',.5,'private_only',evidence_strength=strength,snippets=['source fact'])
        frozen=a.freeze_grounding_evidence(raw,redact=lambda s:s)
        plan=r.build_turn_plan(r.GptTurnInput(request,sections,frozen,'fake',200,r.PRIVATE_POLICY_REVISION))
        assert plan.evidence.to_verifier_evidence().lane_visible_to=='private_only'
        assert plan.evidence.to_verifier_evidence().evidence_strength==strength


def test_canonical_manifest_status_does_not_reread_legacy_owners():
    _,_,_,_,r,_,_,request,sections=fixture()
    plan=r.build_turn_plan(r.GptTurnInput(request,sections,None,'fake',200,r.PRIVATE_POLICY_REVISION))
    audit=importlib.import_module('nana.runtime.context_budget_audit')
    audit.record_compiled_budget(plan.compiled)
    import sys
    sys.modules['nana.config'].NANA_CONTEXT_PRIVATE_MODE='canonical'
    report=audit.build_context_budget_audit('private_owner')
    assert report.total_chars==plan.compiled.manifest.input_chars
    assert report.to_dict()['measurement']=='compiled_manifest'


def test_budget_recording_failure_cannot_abort_or_change_compilation(monkeypatch):
    _,_,_,_,r,_,_,request,sections=fixture()
    inputs=r.GptTurnInput(request,sections,None,'fake',200,r.PRIVATE_POLICY_REVISION)
    expected=r.build_turn_plan(inputs).compiled
    audit=importlib.import_module('nana.runtime.context_budget_audit')
    def unavailable(*_args):
        raise RuntimeError('metadata recorder unavailable')
    monkeypatch.setattr(audit,'record_compiled_budget',unavailable)
    actual=r.build_turn_plan(inputs).compiled
    assert actual.messages==expected.messages
    assert actual.full_context_hash==expected.full_context_hash


def test_operator_only_evidence_is_rejected_in_private_plan():
    _,_,_,c,r,a,_,request,sections=fixture()
    evidence=a.FrozenGroundingEvidence('found',.9,'operator_only','strong',('operator fact',),False,'',())
    with pytest.raises(c.ContextContractError,match='grounding_visibility_denied'):
        r.build_turn_plan(r.GptTurnInput(request,sections,evidence,'fake',200,r.PRIVATE_POLICY_REVISION))


def test_structured_payload_keys_are_redacted_before_compile():
    _,_,_,_,r,_,_,request,sections=fixture()
    sections=tuple(replace(s,payload={'sk-FAKESECRET1234567890':'value'}) if s.id=='identity.owner.v1' else s for s in sections)
    # The common fake fixture intentionally uses identity redaction; load the
    # actual pure redactor here without config/source-store imports.
    from pathlib import Path
    import importlib.util
    spec=importlib.util.spec_from_file_location('task7_real_privacy',Path(__file__).resolve().parents[1]/'runtime/history_privacy.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    plan=r.build_turn_plan(r.GptTurnInput(request,sections,None,'fake',200,r.PRIVATE_POLICY_REVISION),redact=module.redact_history_text)
    assert 'sk-FAKESECRET1234567890' not in repr(plan.compiled.messages)


def test_grounding_snapshot_is_sanitized_once_for_model_and_verifier():
    _,_,_,_,r,a,_,request,sections=fixture()
    from types import SimpleNamespace
    raw=SimpleNamespace(status='found',confidence=.9,lane_visible_to='private_only',
        evidence_strength='strong',snippets=['fact-fixture'],has_legacy_timestamp=False,
        notes='note-fixture',source_event_ids=['event-fixture'])
    calls=[]
    def non_idempotent(value):
        calls.append(value)
        return 'x:'+value
    frozen=a.freeze_grounding_evidence(raw,redact=non_idempotent)
    plan=r.build_turn_plan(r.GptTurnInput(request,sections,frozen,'fake',200,r.PRIVATE_POLICY_REVISION),
                          redact=non_idempotent)
    assert plan.evidence is frozen
    assert calls.count('fact-fixture')==calls.count('note-fixture')==calls.count('event-fixture')==1
    assert 'x:fact-fixture' not in calls and 'x:note-fixture' not in calls and 'x:event-fixture' not in calls
    assert not any(value.startswith('MEMORY EVIDENCE:') for value in calls)
    verifier=plan.evidence.to_verifier_evidence()
    assert verifier.snippets==['x:fact-fixture']
    assert verifier.notes=='x:note-fixture' and verifier.source_event_ids==['x:event-fixture']
    assert verifier.to_prompt_block() in plan.compiled.messages[0].content
    assert plan.compiled.messages[0].content.count('MEMORY EVIDENCE:')==1
