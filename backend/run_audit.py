from database import LocalSession, engine, Branch, Category, ActualTraffic, DailyForecast, IngestedFile, TrainingRun, Event
from sqlalchemy import func

def run_db_audit():
    db = LocalSession()
    print('=====================================================================')
    print('          WAVECAST FORECASTING ENGINE -- DATABASE AUDIT REPORT        ')
    print('=====================================================================')
    
    print('')
    print('[1. TABLE ROW COUNTS]')
    counts = {
        'branches': db.query(Branch).count(),
        'categories': db.query(Category).count(),
        'ingested_files': db.query(IngestedFile).count(),
        'training_runs': db.query(TrainingRun).count(),
        'actual_traffic': db.query(ActualTraffic).count(),
        'daily_forecasts': db.query(DailyForecast).count(),
        'events': db.query(Event).count(),
    }
    for tbl, cnt in counts.items():
        print(f'  - {tbl:<20}: {cnt:,} rows')
        
    print('')
    print('[2. INGESTED FILES TRACKING]')
    files = db.query(IngestedFile).order_by(IngestedFile.ingested_at.desc()).all()
    for f in files:
        print(f'  - File #{f.id}: {f.filename:<35} | Rows: {f.row_count:<6} | Ingested: {f.ingested_at}')
        
    print('')
    print('[3. TRAINING RUNS HISTORY]')
    runs = db.query(TrainingRun).order_by(TrainingRun.id.desc()).all()
    for r in runs:
        min_d = db.query(func.min(DailyForecast.date)).filter(DailyForecast.training_run_id == r.id).scalar()
        max_d = db.query(func.max(DailyForecast.date)).filter(DailyForecast.training_run_id == r.id).scalar()
        fcst_cnt = db.query(DailyForecast).filter(DailyForecast.training_run_id == r.id).count()
        print(f'  - Run #{r.id}: Status={r.status:<7} | Horizon={min_d} --> {max_d} ({fcst_cnt:,} rows)')
        
    print('')
    print('[4. GROUND-TRUTH ACTUAL TRAFFIC SUMMARY]')
    branches = db.query(Branch).all()
    for b in branches:
        act_cnt = db.query(ActualTraffic).filter(ActualTraffic.branch_id == b.id, ActualTraffic.category_id == 0).count()
        min_act = db.query(func.min(ActualTraffic.date)).filter(ActualTraffic.branch_id == b.id, ActualTraffic.category_id == 0).scalar()
        max_act = db.query(func.max(ActualTraffic.date)).filter(ActualTraffic.branch_id == b.id, ActualTraffic.category_id == 0).scalar()
        tot_vol = db.query(func.sum(ActualTraffic.actual_count)).filter(ActualTraffic.branch_id == b.id, ActualTraffic.category_id == 0).scalar() or 0
        print(f'  - Branch: {b.name:<15} | Days: {act_cnt:<3} | Date Span: {min_act} --> {max_act} | Total Tickets: {tot_vol:,}')
        
    print('')
    print('=====================================================================')
    db.close()

if __name__ == '__main__':
    run_db_audit()
