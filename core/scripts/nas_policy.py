"""Pure policy rules shared by NAS scheduling, status and observation."""
import sys as _nas_sys
_nas_sys.dont_write_bytecode=True
import datetime as dt

def due_time(hour, now):
    if not isinstance(hour,int) or not 0 <= hour <= 23:
        raise ValueError('backup_hour must be 0..23')
    scheduled=now.replace(hour=hour,minute=0,second=0,microsecond=0)
    return scheduled if now >= scheduled else scheduled-dt.timedelta(days=1)

def last_success_time(record):
    try:
        return dt.datetime.strptime(record.get('last_success',''),'%Y%m%d-%H%M%S')
    except (TypeError,ValueError):
        return None

def backup_due(config, record, now):
    last=last_success_time(record)
    return last is None or last < due_time(config['backup_hour'],now)

def backup_summary(config, record, now, enabled=True):
    last=last_success_time(record)
    result=dict(record)
    result.update(schedule_enabled=enabled,due_since=due_time(config['backup_hour'],now).isoformat(),overdue=enabled and backup_due(config,record,now))
    result['age_seconds']=max(0,int((now-last).total_seconds())) if last else None
    result['retention']={'keep':config.get('backup_keep',14),'automatic_deletion':False}
    return result

def observation(previous, now, *, problem=None, paused=False, boot_id='', gap_seconds=1200, storage_generation=''):
    """Preserve interruptions; restart a fresh continuous window after a gap/fault."""
    history=list(previous.get('history',[]))
    # Migrate the previous version's errors without erasing the failed observation.
    if not history:
        history.extend(previous.get('errors',[]))
    last=previous.get('last')
    gap=last is not None and (now < last or now-last > gap_seconds)
    legacy=bool(previous and previous.get('schema')!=2)
    if legacy:history.append({'time':now,'type':'policy_migration','previous_state':previous.get('state')})
    changed_boot=bool(previous.get('boot_id') and boot_id and previous['boot_id']!=boot_id)
    storage_changed=bool(storage_generation and previous.get('storage_generation')!=storage_generation)
    if storage_changed:history.append({'time':now,'type':'storage_change','previous_generation':previous.get('storage_generation'),'generation':storage_generation})
    interrupted=legacy or storage_changed or gap or changed_boot or previous.get('state') in ('fault','paused','interrupted')
    if gap or changed_boot:
        history.append({'time':now,'type':'gap' if gap else 'restart','previous_last':last,'gap_seconds':now-last if last else None})
    window_start=now if interrupted or not last else previous.get('window_started',previous.get('started',now))
    samples=0 if interrupted else previous.get('samples',0)
    if paused:
        state='paused';samples=0;window_start=now
    elif problem:
        state='fault';samples=0;window_start=now
        history.append({'time':now,'type':'probe_failure','error':problem})
    else:
        samples+=1
        state='24h_probes_passed' if now-window_start>=86400 and samples>=144 else 'observing'
    return {'schema':2,'state':state,'boot_id':boot_id,'storage_generation':storage_generation or previous.get('storage_generation',''),'window_started':window_start,'last':now,'samples':samples,'window_seconds':max(0,int(now-window_start)),'history':history[-100:],'last_problem':problem,'scope':'Periodic readiness/database/media probes; not a full batch-load certification'}


def mirror_due(config, backup_record, mirror_record):
    """Retry only against an already successful consistent local snapshot."""
    return bool(config.get('hdd_root') and backup_record.get('state')=='success' and last_success_time(backup_record) and (mirror_record.get('source')!=backup_record.get('last_success') or mirror_record.get('state') in ('failed','running','interrupted')))
