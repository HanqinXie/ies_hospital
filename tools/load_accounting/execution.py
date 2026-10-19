"""New execution identity; every physical transition delegates to M19."""
from tools.model_closeout.environment import RecoveryCommandCore as PreviousCore

MODEL_ID = 'M20_load_accounting'
DYNAMICS_CONTRACT_ID = 'hospital_ies.load_accounting_execution.v20.v1'
LOAD_ACCOUNTING_CONTRACT_ID = 'hospital_ies.load_accounting.v20.v1'


class LoadAccountingCore(PreviousCore):
    def generate_command(self, action):
        command = super().generate_command(action)
        command['execution_model_id'] = MODEL_ID
        return command

    def observe(self):
        value = super().observe()
        value['diesel_local_response']['model_id'] = MODEL_ID
        value['execution_recovery']['contract_id'] = DYNAMICS_CONTRACT_ID
        value['model_closeout']['contract_id'] = DYNAMICS_CONTRACT_ID
        value['load_accounting'] = dict(contract_id=LOAD_ACCOUNTING_CONTRACT_ID,
            original_input_bytes_unchanged=True, grouping_changed=True)
        return value

    def advance(self, end):
        first = len(self.segments)
        try:
            return super().advance(end)
        finally:
            for segment in self.segments[first:]:
                segment['execution_model_id'] = MODEL_ID
                segment['load_accounting_contract_id'] = LOAD_ACCOUNTING_CONTRACT_ID
