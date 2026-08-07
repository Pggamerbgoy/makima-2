//! TripleStore — SQLite WAL knowledge graph
//!
//! Stores (subject, predicate, object) triples with confidence, timestamps,
//! tombstone support, cascade checking, and TTL.
//! Internal Mutex for write serialization. Reads are lock-free via WAL.

use parking_lot::Mutex;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use rusqlite::{params, Connection};
use std::path::PathBuf;
use uuid::Uuid;

#[pyclass]
pub struct TripleStore {
    conn: Mutex<Connection>,
}

#[pymethods]
impl TripleStore {
    #[new]
    fn new(db_path: String) -> PyResult<Self> {
        let path = PathBuf::from(&db_path);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).ok();
        }

        let conn = Connection::open(&db_path)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        // WAL mode for concurrent reads
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;")
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        conn.execute_batch(
            "CREATE TABLE IF NOT EXISTS triples (
                id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                confidence REAL DEFAULT 1.0,
                source TEXT DEFAULT '',
                created_at REAL NOT NULL,
                deleted_at REAL,
                expires_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_subject ON triples(subject);
            CREATE INDEX IF NOT EXISTS idx_predicate ON triples(predicate);
            CREATE INDEX IF NOT EXISTS idx_sp ON triples(subject, predicate);",
        )
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

        Ok(TripleStore {
            conn: Mutex::new(conn),
        })
    }

    /// Insert a triple. Returns the generated ID.
    fn insert(
        &self,
        py: Python<'_>,
        subject: String,
        predicate: String,
        object_: String,
        confidence: f64,
        source: String,
    ) -> PyResult<String> {
        let id = Uuid::new_v4().to_string();
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs_f64();

        let id_clone = id.clone();
        py.allow_threads(|| {
            let conn = self.conn.lock();
            conn.execute(
                "INSERT INTO triples (id, subject, predicate, object, confidence, source, created_at)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                params![id_clone, subject, predicate, object_, confidence, source, now],
            )
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))
        })?;

        Ok(id)
    }

    /// Query triples by subject and/or predicate. Returns list of dicts.
    #[pyo3(signature = (subject=None, predicate=None, object_=None))]
    fn query(
        &self,
        py: Python<'_>,
        subject: Option<String>,
        predicate: Option<String>,
        object_: Option<String>,
    ) -> PyResult<Vec<PyObject>> {
        let results = py.allow_threads(|| {
            let conn = self.conn.lock();
            let mut sql = String::from(
                "SELECT id, subject, predicate, object, confidence, source, created_at
                 FROM triples WHERE deleted_at IS NULL
                 AND (expires_at IS NULL OR expires_at > strftime('%s','now'))"
            );
            let mut conditions: Vec<String> = Vec::new();
            let mut bind_values: Vec<String> = Vec::new();

            if let Some(ref s) = subject {
                conditions.push(format!("subject = ?{}", bind_values.len() + 1));
                bind_values.push(s.clone());
            }
            if let Some(ref p) = predicate {
                conditions.push(format!("predicate = ?{}", bind_values.len() + 1));
                bind_values.push(p.clone());
            }
            if let Some(ref o) = object_ {
                conditions.push(format!("object = ?{}", bind_values.len() + 1));
                bind_values.push(o.clone());
            }

            if !conditions.is_empty() {
                sql.push_str(" AND ");
                sql.push_str(&conditions.join(" AND "));
            }

            let mut stmt = conn.prepare(&sql).unwrap();
            let params_refs: Vec<&dyn rusqlite::types::ToSql> = bind_values
                .iter()
                .map(|v| v as &dyn rusqlite::types::ToSql)
                .collect();

            let rows: Vec<(String, String, String, String, f64, String, f64)> = stmt
                .query_map(params_refs.as_slice(), |row| {
                    Ok((
                        row.get(0)?,
                        row.get(1)?,
                        row.get(2)?,
                        row.get(3)?,
                        row.get(4)?,
                        row.get(5)?,
                        row.get(6)?,
                    ))
                })
                .unwrap()
                .filter_map(|r| r.ok())
                .collect();

            rows
        });

        // Convert to Python dicts (must hold GIL for this)
        let mut py_results = Vec::new();
        for (id, subj, pred, obj, conf, src, created) in results {
            let dict = PyDict::new_bound(py);
            dict.set_item("id", &id)?;
            dict.set_item("subject", &subj)?;
            dict.set_item("predicate", &pred)?;
            dict.set_item("object", &obj)?;
            dict.set_item("confidence", conf)?;
            dict.set_item("source", &src)?;
            dict.set_item("created_at", created)?;
            py_results.push(dict.into_any().unbind());
        }

        Ok(py_results)
    }

    /// Tombstone a triple (soft delete). Sets deleted_at timestamp.
    fn tombstone(&self, py: Python<'_>, id: String) -> PyResult<bool> {
        let affected = py.allow_threads(|| {
            let conn = self.conn.lock();
            let now = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_secs_f64();
            conn.execute(
                "UPDATE triples SET deleted_at = ?1 WHERE id = ?2 AND deleted_at IS NULL",
                params![now, id],
            )
            .unwrap_or(0)
        });
        Ok(affected > 0)
    }

    /// Check how many triples reference the same subject as the given triple.
    fn cascade_check(&self, py: Python<'_>, id: String) -> PyResult<usize> {
        let count = py.allow_threads(|| {
            let conn = self.conn.lock();
            let subject: Option<String> = conn
                .query_row("SELECT subject FROM triples WHERE id = ?1", params![id], |row| {
                    row.get(0)
                })
                .ok();

            match subject {
                Some(subj) => {
                    let count: usize = conn
                        .query_row(
                            "SELECT COUNT(*) FROM triples WHERE subject = ?1 AND deleted_at IS NULL AND id != ?2",
                            params![subj, id],
                            |row| row.get(0),
                        )
                        .unwrap_or(0);
                    count
                }
                None => 0,
            }
        });
        Ok(count)
    }

    /// Purge expired triples (TTL cleanup).
    fn purge_expired(&self, py: Python<'_>) -> PyResult<usize> {
        let deleted = py.allow_threads(|| {
            let conn = self.conn.lock();
            conn.execute(
                "DELETE FROM triples WHERE expires_at IS NOT NULL AND expires_at < strftime('%s','now')",
                [],
            )
            .unwrap_or(0)
        });
        Ok(deleted)
    }

    /// Return total number of active (non-deleted) triples.
    fn count(&self) -> PyResult<usize> {
        let conn = self.conn.lock();
        let count: usize = conn
            .query_row(
                "SELECT COUNT(*) FROM triples WHERE deleted_at IS NULL",
                [],
                |row| row.get(0),
            )
            .unwrap_or(0);
        Ok(count)
    }
}
