//! Checkpointer — CBOR atomic write/restore
//!
//! Serializes conversation state to CBOR format.
//! Atomic write: tmp file → fsync → rename.
//! Used by StateCheckpointer in Python brain every 15s.

use pyo3::prelude::*;
use std::fs;
use std::io::Write;
use std::path::PathBuf;

#[pyclass]
pub struct Checkpointer {
    dir: PathBuf,
}

#[pymethods]
impl Checkpointer {
    #[new]
    fn new(dir: String) -> PyResult<Self> {
        let path = PathBuf::from(&dir);
        fs::create_dir_all(&path)
            .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
        Ok(Checkpointer { dir: path })
    }

    /// Save state as CBOR with atomic write (tmp → fsync → rename).
    fn save(&self, py: Python<'_>, name: String, data: Vec<u8>) -> PyResult<String> {
        let final_path = self.dir.join(format!("{}.cbor", name));
        let tmp_path = self.dir.join(format!("{}.cbor.tmp", name));

        py.allow_threads(|| -> PyResult<()> {
            // Encode as CBOR
            let cbor_data = serde_cbor::to_vec(&serde_cbor::Value::Bytes(data))
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

            // Write to tmp
            let mut file = fs::File::create(&tmp_path)
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
            file.write_all(&cbor_data)
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
            file.sync_all()
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

            // Atomic rename
            fs::rename(&tmp_path, &final_path)
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

            Ok(())
        })?;

        Ok(final_path.to_string_lossy().to_string())
    }

    /// Restore state from CBOR file. Returns raw bytes.
    fn restore(&self, py: Python<'_>, name: String) -> PyResult<Option<Vec<u8>>> {
        let path = self.dir.join(format!("{}.cbor", name));

        if !path.exists() {
            return Ok(None);
        }

        let data = py.allow_threads(|| {
            let raw = fs::read(&path)
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

            let value: serde_cbor::Value = serde_cbor::from_slice(&raw)
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;

            match value {
                serde_cbor::Value::Bytes(b) => Ok::<Option<Vec<u8>>, PyErr>(Some(b)),
                _ => Ok::<Option<Vec<u8>>, PyErr>(None),
            }
        })?;

        Ok(data)
    }

    /// List all checkpoint files.
    fn list_checkpoints(&self) -> PyResult<Vec<String>> {
        let mut names = Vec::new();
        if let Ok(entries) = fs::read_dir(&self.dir) {
            for entry in entries.flatten() {
                let name = entry.file_name().to_string_lossy().to_string();
                if name.ends_with(".cbor") && !name.ends_with(".tmp") {
                    names.push(name.trim_end_matches(".cbor").to_string());
                }
            }
        }
        Ok(names)
    }

    /// Delete a checkpoint.
    fn delete(&self, name: String) -> PyResult<bool> {
        let path = self.dir.join(format!("{}.cbor", name));
        if path.exists() {
            fs::remove_file(&path)
                .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
            Ok(true)
        } else {
            Ok(false)
        }
    }
}
