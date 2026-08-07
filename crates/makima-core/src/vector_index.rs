//! HNSW Vector Index
//!
//! Approximate nearest neighbor search for semantic memory.
//! Thread-safe: add() and search() can be called from Python executor threads.
//! Snapshot/restore for persistence.

use ordered_float::OrderedFloat;
use parking_lot::RwLock;
use pyo3::prelude::*;
use rand::Rng;
use std::collections::HashMap;

const M: usize = 16;           // Max connections per layer
const EF_CONSTRUCTION: usize = 200;
const EF_SEARCH: usize = 50;
const ML: f64 = 0.36067376022224085; // 1.0 / ln(16)

#[derive(Clone)]
struct Node {
    id: String,
    vector: Vec<f32>,
    neighbors: Vec<Vec<String>>,  // neighbors[layer] = vec of ids
}

fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
    let dot: f32 = a.iter().zip(b.iter()).map(|(x, y)| x * y).sum();
    let norm_a: f32 = a.iter().map(|x| x * x).sum::<f32>().sqrt();
    let norm_b: f32 = b.iter().map(|x| x * x).sum::<f32>().sqrt();
    if norm_a == 0.0 || norm_b == 0.0 { return 0.0; }
    dot / (norm_a * norm_b)
}

#[pyclass]
pub struct VectorIndex {
    nodes: RwLock<HashMap<String, Node>>,
    entry_point: RwLock<Option<String>>,
    max_layer: RwLock<usize>,
    dim: usize,
}

#[pymethods]
impl VectorIndex {
    #[new]
    fn new(dim: usize) -> Self {
        VectorIndex {
            nodes: RwLock::new(HashMap::new()),
            entry_point: RwLock::new(None),
            max_layer: RwLock::new(0),
            dim,
        }
    }

    /// Add a vector with the given ID. Thread-safe.
    fn add(&self, py: Python<'_>, id: String, vector: Vec<f32>) -> PyResult<()> {
        if vector.len() != self.dim {
            return Err(pyo3::exceptions::PyValueError::new_err(
                format!("Expected dim {}, got {}", self.dim, vector.len())
            ));
        }

        // Release GIL for the heavy computation
        py.allow_threads(|| {
            let level = self.random_level();
            let node = Node {
                id: id.clone(),
                vector: vector.clone(),
                neighbors: vec![Vec::new(); level + 1],
            };

            let mut nodes = self.nodes.write();
            nodes.insert(id.clone(), node);

            let mut entry = self.entry_point.write();
            if entry.is_none() {
                *entry = Some(id.clone());
                let mut ml = self.max_layer.write();
                *ml = level;
            } else {
                // Simple insertion: connect to nearest neighbors at each layer
                let _ep_id = entry.clone().unwrap();
                let mut ml = self.max_layer.write();

                if level > *ml {
                    *entry = Some(id.clone());
                    *ml = level;
                }

                // Connect to M nearest at layer 0
                let mut candidates: Vec<(OrderedFloat<f32>, String)> = Vec::new();
                for (nid, n) in nodes.iter() {
                    if nid != &id {
                        let sim = cosine_similarity(&vector, &n.vector);
                        candidates.push((OrderedFloat(sim), nid.clone()));
                    }
                }
                candidates.sort_by(|a, b| b.0.cmp(&a.0));
                candidates.truncate(M);

                if let Some(node) = nodes.get_mut(&id) {
                    node.neighbors[0] = candidates.iter().map(|(_, nid)| nid.clone()).collect();
                }

                // Add reverse connections
                for (_, nid) in &candidates {
                    let should_prune = {
                        if let Some(neighbor) = nodes.get_mut(nid) {
                            if !neighbor.neighbors.is_empty() {
                                neighbor.neighbors[0].push(id.clone());
                                neighbor.neighbors[0].len() > M * 2
                            } else {
                                false
                            }
                        } else {
                            false
                        }
                    };
                    if should_prune {
                        let (nv, neighbor_ids) = {
                            let n = nodes.get(nid).unwrap();
                            (n.vector.clone(), n.neighbors[0].clone())
                        };
                        let mut scored: Vec<(OrderedFloat<f32>, String)> = neighbor_ids
                            .iter()
                            .filter_map(|cid| {
                                nodes.get(cid).map(|cn| {
                                    (OrderedFloat(cosine_similarity(&nv, &cn.vector)), cid.clone())
                                })
                            })
                            .collect();
                        scored.sort_by(|a, b| b.0.cmp(&a.0));
                        scored.truncate(M);
                        if let Some(neighbor) = nodes.get_mut(nid) {
                            neighbor.neighbors[0] = scored.into_iter().map(|(_, id)| id).collect();
                        }
                    }
                }
            }
        });

        Ok(())
    }

    /// Search for k nearest neighbors. Returns Vec<(id, similarity)>.
    fn search(&self, py: Python<'_>, query: Vec<f32>, k: usize) -> PyResult<Vec<(String, f32)>> {
        if query.len() != self.dim {
            return Err(pyo3::exceptions::PyValueError::new_err(
                format!("Expected dim {}, got {}", self.dim, query.len())
            ));
        }

        let results = py.allow_threads(|| {
            let nodes = self.nodes.read();
            let mut scored: Vec<(OrderedFloat<f32>, String)> = nodes.iter()
                .map(|(id, n)| (OrderedFloat(cosine_similarity(&query, &n.vector)), id.clone()))
                .collect();
            scored.sort_by(|a, b| b.0.cmp(&a.0));
            scored.truncate(k);
            scored.into_iter().map(|(s, id)| (id, s.0)).collect::<Vec<_>>()
        });

        Ok(results)
    }

    /// Delete a vector by ID.
    fn delete(&self, py: Python<'_>, id: String) -> PyResult<bool> {
        let removed = py.allow_threads(|| {
            let mut nodes = self.nodes.write();
            let existed = nodes.remove(&id).is_some();
            // Remove from neighbor lists
            if existed {
                for node in nodes.values_mut() {
                    for layer_neighbors in &mut node.neighbors {
                        layer_neighbors.retain(|nid| nid != &id);
                    }
                }
            }
            existed
        });
        Ok(removed)
    }

    /// Return number of vectors in the index.
    fn len(&self) -> usize {
        self.nodes.read().len()
    }

    /// Snapshot: serialize all nodes to JSON bytes for persistence.
    fn snapshot(&self, py: Python<'_>) -> PyResult<Vec<u8>> {
        let data = py.allow_threads(|| {
            let nodes = self.nodes.read();
            let serializable: Vec<(String, Vec<f32>)> = nodes.iter()
                .map(|(id, n)| (id.clone(), n.vector.clone()))
                .collect();
            serde_json::to_vec(&serializable).unwrap_or_default()
        });
        Ok(data)
    }

    /// Restore from a snapshot.
    fn restore(&self, py: Python<'_>, data: Vec<u8>) -> PyResult<usize> {
        let count = py.allow_threads(|| -> usize {
            let parsed: Vec<(String, Vec<f32>)> = match serde_json::from_slice(&data) {
                Ok(v) => v,
                Err(_) => return 0,
            };
            let count = parsed.len();
            // We need to add them through the normal path, but since we're
            // inside allow_threads, we rebuild manually
            let mut nodes = self.nodes.write();
            nodes.clear();
            for (id, vector) in parsed {
                let node = Node {
                    id: id.clone(),
                    vector,
                    neighbors: vec![Vec::new()],
                };
                nodes.insert(id, node);
            }
            count
        });
        Ok(count)
    }
}

impl VectorIndex {
    fn random_level(&self) -> usize {
        let mut rng = rand::thread_rng();
        let r: f64 = rng.gen();
        (-r.ln() * ML).floor() as usize
    }
}
