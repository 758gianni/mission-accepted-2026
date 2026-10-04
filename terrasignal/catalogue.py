"""SQLite acquisition/asset catalogue; raw references are immutable once registered."""
import json
from pathlib import Path
import sqlite3


class Catalogue:
    def __init__(self,path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS observations (
                sensor TEXT NOT NULL, source_record_id TEXT NOT NULL,
                acquisition_iso TEXT NOT NULL, metadata_json TEXT NOT NULL,
                PRIMARY KEY(sensor,source_record_id));
            CREATE INDEX IF NOT EXISTS acquisition_time ON observations(sensor,acquisition_iso);
            CREATE TABLE IF NOT EXISTS raw_assets (
                sensor TEXT NOT NULL, source_record_id TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE, bytes INTEGER NOT NULL,
                crc_verified INTEGER NOT NULL, sha256 TEXT,
                PRIMARY KEY(sensor,source_record_id,path));
        ''')

    def register_observations(self,observations):
        with self.db:
            self.db.executemany('INSERT INTO observations VALUES (?,?,?,?) ON CONFLICT(sensor,source_record_id) DO UPDATE SET acquisition_iso=excluded.acquisition_iso,metadata_json=excluded.metadata_json',
                [(o.sensor,o.source_record_id,o.acquisition_iso,json.dumps(o.to_dict())) for o in observations])

    def register_raw(self,sensor,record_id,path, *, crc_verified,sha256=None):
        path=Path(path).resolve()
        size=path.stat().st_size
        if not crc_verified:
            raise ValueError('Only CRC-verified archives enter the immutable RAW tier')
        with self.db:
            row=self.db.execute('SELECT sensor,source_record_id,bytes,sha256 FROM raw_assets WHERE path=?',(str(path),)).fetchone()
            if row and (row[:3]!=(sensor,record_id,size) or (row[3] and sha256 and row[3]!=sha256)):
                raise ValueError('Immutable raw reference changed; do not overwrite catalogue provenance')
            self.db.execute('INSERT OR IGNORE INTO raw_assets VALUES (?,?,?,?,?,?)',(sensor,record_id,str(path),size,1,sha256))
            if sha256:
                self.db.execute('UPDATE raw_assets SET sha256=? WHERE path=? AND sha256 IS NULL',(sha256,str(path)))

    def import_journals(self,raw_dir):
        imported=0
        for path in Path(raw_dir).glob('orders-*.json'):
            data=json.loads(path.read_text())
            for rid,assets in data.get('archives',{}).items():
                for a in assets:
                    if Path(a['path']).is_file() and Path(a['path']).stat().st_size==a['bytes'] and a.get('crc_verified'):
                        self.register_raw('RADARSAT-2',rid,a['path'],crc_verified=True,sha256=a.get('sha256'))
                        imported+=1
        return imported

    def close(self):
        self.db.close()
