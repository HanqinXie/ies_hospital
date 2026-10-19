"""Evidence-aware algorithm registration. Registration does not implement/train."""
from __future__ import annotations
import copy

def registry():
    def entry(view,output,unit,source,location,status='candidate',online=False,limit='No hospital fit or independent performance validation'):
        return dict(input_view=view,output_meaning=output,output_unit=unit,source=source,
            source_location=location,evidence_map='taskP2/ALGORITHM_EVIDENCE_MAP.md',
            source_verification='provided evidence map; no new full-text reproduction claimed',
            fit_dataset_ids=[],dependent_distribution_id=None,dependent_policy_id=None,
            online_control_allowed=online,status=status,error_metrics=None,scope_limit=limit)
    records={
        'independent_design':entry('scenario','independent conditional pressure design','design coordinates',
            'project R07/R08 specification','P2A_CODEX_TASK sections3/4',status='implemented_design',
            limit='q_design only; not calibrated accident frequencies'),
        'gaussian_copula':entry('scenario','joint exogenous parameters','marginal units','D24; Z07','D24 PDF4-7; Z07 sections3-4'),
        't_copula':entry('scenario','joint exogenous parameters','marginal units','project optional candidate','P2A section7'),
        'vine_copula':entry('scenario','joint exogenous parameters','marginal units','project optional candidate','P2A section7'),
        'dnipc':entry('scenario','offline continuous consequences','kWh / degC separately','D22 10.1016/j.ast.2022.107802','PDF3,5-8; Appendix B PDF16'),
        'ols_pce':entry('scenario','offline continuous consequences','kWh / degC separately','D22; D24 comparator','D24 PDF11 tables3-4'),
        'lar_pce':entry('scenario','offline sparse regression consequences','kWh / degC separately','project comparator','P2A section7; NOT D-MORPH'),
        'd_morph_dspce':entry('scenario','offline sparse chaos consequences','kWh / degC separately','D24 10.1016/j.cja.2024.04.001','PDF7 eq9-11; PDF8 eq12-20; PDF9 eq23-27'),
        'rf':entry('decision','policy-conditional first observed failure','probability under named design','project baseline','P2A section7'),
        'mlp':entry('decision','policy-conditional first observed failure','probability under named design','project candidate','P2A section7'),
        'gru':entry('decision','history-conditional first observed failure','probability under named design','project candidate','P2A section7'),
        'tcn':entry('decision','history-conditional first observed failure','probability under named design','project candidate','P2A section7'),
        'graph_temporal':entry('graph','history and graph conditional risk','probability under named design','Z05; Z13','Z05 PDF3-5 sections3-5; Z13 dataset abstract'),
        'priority_milp':entry('decision','projected command for frozen actuator queue','six targets kW','existing project MILP','resilience._solve_milp',status='implemented_baseline',online=True,
            limit='Frozen lexicographic single-step baseline, not globally safe controller'),
        'secure_milp':entry('decision','same constraints plus final GT loading penalty','six targets kW','existing project MILP','resilience._solve_milp',status='implemented_baseline',online=True,
            limit='GT penalty proxy; no general benefit or expert safety claim'),
        'candidate_action_risk':entry('decision_action_branch','action-conditional consequences','carrier-specific consequences','Z18','PDF3-4 section4; PDF7'),
        'safe_rl':entry('decision','constrained policy','six targets if separately approved','Z11; Z19; Z20','Z11 PDF3-5; Z19 PDF3-5; Z20 method mapping'),
        'dro_miqcp':entry('scenario_action','robust dispatch','future action interface','Z18','PDF3-4,7; MIQCP architecture requires separate authorization'),
    }
    return copy.deepcopy(records)

def validate_method_use(method_id,*,view,online_control=False,fit_dataset_ids=(),fit_splits=(),
                        distribution_id=None,policy_id=None):
    records=registry()
    if method_id not in records:raise ValueError('Unregistered method')
    r=records[method_id]
    if view!=r['input_view']:raise ValueError('Method input view mismatch (offline future script cannot enter online model)')
    if online_control and not r['online_control_allowed']:raise ValueError('Candidate has no authorized online-control adapter')
    if fit_dataset_ids:
        if not distribution_id or not policy_id:raise ValueError('Fitted artifact requires distribution and policy provenance')
        if len(fit_dataset_ids)!=len(fit_splits) or set(fit_splits)-{'train'}:raise ValueError('Fitted transforms require train-only provenance')
        raise ValueError('P2A registry has no authorized hospital fitting backend')
    return r
