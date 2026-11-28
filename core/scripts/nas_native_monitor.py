"""Release only owned backup references when CoW retention threatens business space."""
from pathlib import Path
from nas_backup_control import status,cancel
from nas_snapshot_budget import BudgetExceeded


def maintain(c):
 from nas_native import Manager,enabled
 if not enabled(c):return
 from nasctl import lock,audit
 manager=Manager(c,capacity_check=False)
 state=manager.state.snapshot()
 if not manager.budget.group:raise BudgetExceeded('Native checkpoint accounting is not prepared')
 try:
  manager.budget.check()
  if len(state['checkpoints'])>=8:raise BudgetExceeded('Local checkpoint count budget exhausted')
 except BudgetExceeded as error:
  if not state.get('drop_requested'):
   manager.state.update(lambda s:s.update(drop_requested=True,drop_reason=str(error)))
   audit(c,'native-checkpoint-reclamation-requested',str(error))
 else:
  if not state.get('drop_requested'):return
 # Never hold state/account locks while asking the active job to finish.
 operation=status(c)['operation']
 if operation.get('state') in ('running','cancelling'):
  if operation.get('kind')=='cloud' and operation['state']=='running':cancel(c,operation['id'])
  return
 with lock(shared=True):
  # A launch may have acquired the shared lifecycle lock concurrently. The
  # persisted drop flag makes its guard fail; wait for its operation to leave.
  if status(c)['operation'].get('state') in ('running','cancelling'):return
  release(manager)
  audit(c,'native-checkpoints-reclaimed',{'cloud_history_deleted':False})


def release(manager):
 """Idempotent across crashes; no unregistered path or source data is removed."""
 state=manager.state.snapshot()
 if not state.get('drop_requested'):raise RuntimeError('Checkpoint reclamation was not requested')
 for ident,point in state['checkpoints'].items():
  if point.get('state')!='ready':
   # A crash between snapshot creation and UUID registration cannot justify
   # guessing ownership; leave the checkpoint and report for reconciliation.
   if manager.snapshots.path(ident).exists():raise RuntimeError('Unregistered checkpoint requires explicit reconciliation')
  elif manager.snapshots.path(ident).exists():manager.snapshots.discard(ident,point['snapshot_uuid'])
  manager.state.update(lambda s,i=ident:s['checkpoints'].pop(i,None))
 def clear(s):
  s['latest']=None
  for account in s['accounts'].values():
   account['data_parent']=None
   # Keep durable upload intents: an immutable remote commit can be published
   # even after its local snapshot was reclaimed. Otherwise data() rebases.
  s.update(drop_requested=False,drop_reason=None)
 manager.state.update(clear)
