import React, { useMemo, useState } from 'react';
import { motion } from 'framer-motion';
import {
  Check,
  Cpu,
  Globe,
  KeyRound,
  Lock,
  Radio,
  Search,
  Server,
  Settings,
  Sparkles,
  X,
  Zap,
} from 'lucide-react';
import type { LLMProvider } from '../types/chat';

interface ModelSelectorPopoverProps {
  isOpen: boolean;
  onClose: () => void;
  providers: LLMProvider[];
  isLoading?: boolean;
  activeProviderId: string;
  activeModel: string;
  onSelectModel: (providerId: string, model: string) => void;
  onOpenFullSettings: () => void;
  clientApiKeys?: Record<string, string>;
  onSaveClientApiKey?: (providerId: string, apiKey: string) => void;
}

export const ModelSelectorPopover: React.FC<ModelSelectorPopoverProps> = ({
  isOpen,
  onClose,
  providers,
  isLoading,
  activeProviderId,
  activeModel,
  onSelectModel,
  onOpenFullSettings,
  clientApiKeys = {},
  onSaveClientApiKey,
}) => {
  const [searchQuery, setSearchQuery] = useState('');
  const [activeCategory, setActiveCategory] = useState<'all' | 'fast' | 'reasoning' | 'local'>('all');
  const [editingKeyForProvider, setEditingKeyForProvider] = useState<string | null>(null);
  const [tempApiKey, setTempApiKey] = useState('');
  const [expandedProviders, setExpandedProviders] = useState<Record<string, boolean>>({});

  // Curated descriptions and badges for known models
  const getModelDescriptor = (providerId: string, modelName: string): { label: string; badge: string } => {
    const m = (modelName || '').toLowerCase();
    if (m.includes('gpt-oss-120b')) return { label: 'GPT-OSS 120B', badge: 'Ultra-Fast · 120B' };
    if (m.includes('gpt-oss-20b')) return { label: 'GPT-OSS 20B', badge: 'Lightning Fast' };
    if (m.includes('qwen3.8') || m.includes('qwen-3.8')) return { label: 'Qwen 3.8 27B', badge: 'Next-Gen Open' };
    if (m.includes('llama-3.3-70b')) return { label: 'Llama 3.3 70B', badge: 'Ultra-Fast · 70B' };
    if (m.includes('llama3-8b') || m.includes('llama-3.1-8b') || m.includes('llama3.2')) return { label: 'Llama 3.2 / 8B', badge: 'Fast · Low Latency' };
    if (m.includes('gemini-2.5-flash') || m.includes('gemini-2.0-flash')) return { label: 'Gemini 2.5 Flash', badge: '1M Context · Multimodal' };
    if (m.includes('gemini-2.5-pro')) return { label: 'Gemini 2.5 Pro', badge: 'Deep Reasoning · 2M' };
    if (m.includes('deepseek-r1')) return { label: 'DeepSeek R1', badge: 'Reasoning CoT' };
    if (m.includes('deepseek-chat') || m.includes('deepseek-v3')) return { label: 'DeepSeek V3', badge: 'Strong Coding · Fast' };
    if (m.includes('qwen') && m.includes('72b')) return { label: 'Qwen 2.5 72B', badge: 'Top Tier Open Weights' };
    if (m.includes('qwen') && m.includes('flash')) return { label: 'Qwen 2.5 Flash', badge: 'Fast Speed' };
    if (m.includes('gpt-4o-mini')) return { label: 'GPT-4o Mini', badge: 'Affordable · Fast' };
    if (m.includes('gpt-4o')) return { label: 'GPT-4o Omnimodal', badge: 'Flagship' };
    if (m.includes('claude-3-5-sonnet') || m.includes('sonnet')) return { label: 'Claude 3.5 Sonnet', badge: 'Top Code & Logic' };
    if (m.includes('claude-3-5-haiku')) return { label: 'Claude 3.5 Haiku', badge: 'Instant Response' };
    return { label: modelName || 'Model', badge: (providerId || 'LLM').toUpperCase() };
  };

  // MUST be called unconditionally before any early return to comply with React Rules of Hooks
  const filteredProviders = useMemo(() => {
    if (!isOpen) return [];
    const q = searchQuery.toLowerCase().trim();
    return (providers || [])
      .map((provider) => {
        if (!provider) return null;
        // Filter by category
        if (activeCategory === 'local' && !provider.local) return null;
        if (activeCategory === 'fast' && !(provider.id === 'groq' || provider.id === 'gemini' || provider.id === 'cerebras')) return null;
        if (activeCategory === 'reasoning' && !(provider.id === 'deepseek' || provider.id === 'openai' || provider.id === 'anthropic' || provider.id === 'gemini')) return null;

        const pName = (provider.name || '').toLowerCase();
        const pId = (provider.id || '').toLowerCase();
        const providerMatches = pName.includes(q) || pId.includes(q);
        const allModels = Array.isArray(provider.models) && provider.models.length > 0 
          ? provider.models 
          : [provider.model || 'default'];
          
        const matchedModels = allModels.filter(
          (m) => providerMatches || (m || '').toLowerCase().includes(q)
        );

        if (matchedModels.length === 0 && !providerMatches) return null;
        return {
          ...provider,
          filteredModels: matchedModels.length > 0 ? matchedModels : allModels,
        };
      })
      .filter((p): p is LLMProvider & { filteredModels: string[] } => Boolean(p));
  }, [isOpen, providers, searchQuery, activeCategory]);

  if (!isOpen) return null;

  const handleSaveKey = (providerId: string) => {
    if (onSaveClientApiKey) {
      onSaveClientApiKey(providerId, tempApiKey.trim());
    }
    setEditingKeyForProvider(null);
    setTempApiKey('');
  };

  return (
    <motion.div
      className="model-popover-backdrop"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.15, ease: [0.16, 1, 0.3, 1] }}
      onClick={onClose}
    >
      <motion.div
        className="model-popover-container"
        initial={{ opacity: 0, scale: 0.96, y: 8 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.97, y: 4 }}
        transition={{ type: 'spring', stiffness: 400, damping: 30 }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Model Selector"
      >
        {/* Header */}
        <div className="model-popover-header">
          <div className="model-popover-title">
            <Sparkles size={16} className="model-popover-sparkle" />
            <span>Select Model & Provider</span>
          </div>
          <button
            type="button"
            className="model-popover-close-btn"
            onClick={onClose}
            aria-label="Close model selector"
          >
            <X size={15} />
          </button>
        </div>

        {/* Search bar */}
        <div className="model-popover-search-wrap">
          <Search size={14} className="model-search-icon" />
          <input
            type="text"
            className="model-popover-search-input"
            placeholder="Search models (e.g., Llama 3.3, Gemini, DeepSeek, Claude)..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            autoFocus
          />
          {searchQuery && (
            <button
              type="button"
              className="model-search-clear-btn"
              onClick={() => setSearchQuery('')}
            >
              <X size={12} />
            </button>
          )}
        </div>

        {/* Category Tabs */}
        <div className="model-popover-tabs">
          <button
            type="button"
            className={`model-category-tab ${activeCategory === 'all' ? 'active' : ''}`}
            onClick={() => setActiveCategory('all')}
          >
            All Providers
          </button>
          <button
            type="button"
            className={`model-category-tab ${activeCategory === 'fast' ? 'active' : ''}`}
            onClick={() => setActiveCategory('fast')}
          >
            <Zap size={11} /> Fast & Free
          </button>
          <button
            type="button"
            className={`model-category-tab ${activeCategory === 'reasoning' ? 'active' : ''}`}
            onClick={() => setActiveCategory('reasoning')}
          >
            <Cpu size={11} /> Reasoning
          </button>
          <button
            type="button"
            className={`model-category-tab ${activeCategory === 'local' ? 'active' : ''}`}
            onClick={() => setActiveCategory('local')}
          >
            <Server size={11} /> Local Offline
          </button>
        </div>

        {/* Providers & Models List */}
        <div className="model-popover-scroll-area">
          {isLoading && providers.length === 0 ? (
            <div className="model-popover-empty">
              <p>Loading models from Makima Brain…</p>
            </div>
          ) : filteredProviders.length === 0 ? (
            <div className="model-popover-empty">
              <p>No matching models or providers found.</p>
              <button
                type="button"
                className="model-popover-link-btn"
                onClick={() => { setSearchQuery(''); setActiveCategory('all'); }}
              >
                Clear search filter
              </button>
            </div>
          ) : (
            filteredProviders.map((provider) => {
              const isCurrentProvider = provider.id === activeProviderId;
              const hasClientKey = Boolean(clientApiKeys[provider.id]);
              const isConfigured = provider.configured || hasClientKey || provider.local;

              return (
                <div
                  key={provider.id}
                  className={`model-provider-group ${isCurrentProvider ? 'active-group' : ''}`}
                >
                  {/* Provider Group Header */}
                  <div className="model-provider-header">
                    <div className="model-provider-identity">
                      <span className="model-provider-mark">
                        {provider.local ? <Server size={13} /> : <Globe size={13} />}
                      </span>
                      <strong className="model-provider-name">{provider.name}</strong>
                      {provider.badge && (
                        <span className="model-provider-badge">{provider.badge}</span>
                      )}
                    </div>

                    <div className="model-provider-actions">
                      <span
                        className={`model-status-indicator ${
                          isConfigured ? 'status-ready' : 'status-needs-key'
                        }`}
                        title={
                          provider.local
                            ? 'Local Ollama - runs offline on device'
                            : isConfigured
                            ? 'Ready to generate'
                            : 'API key not configured yet'
                        }
                      >
                        <span className="status-dot" />
                        {provider.local
                          ? 'Localhost'
                          : hasClientKey
                          ? 'Device Key'
                          : provider.configured
                          ? 'Ready'
                          : 'Needs Key'}
                      </span>

                      {!provider.local && (
                        <button
                          type="button"
                          className="model-byok-toggle-btn"
                          onClick={() => {
                            if (editingKeyForProvider === provider.id) {
                              setEditingKeyForProvider(null);
                            } else {
                              setEditingKeyForProvider(provider.id);
                              setTempApiKey(clientApiKeys[provider.id] || '');
                            }
                          }}
                          title="Set private API key for this device only"
                        >
                          <KeyRound size={12} />
                          <span>{hasClientKey ? 'Edit Key' : '+ Key'}</span>
                        </button>
                      )}
                    </div>
                  </div>

                  {/* Inline BYOK Key Input */}
                  {editingKeyForProvider === provider.id && (
                    <div className="model-inline-byok-box">
                      <div className="model-byok-label">
                        <Lock size={11} />
                        <span>Private Device API Key (stored in your browser only):</span>
                      </div>
                      <div className="model-byok-row">
                        <input
                          type="password"
                          className="model-byok-input"
                          placeholder={provider.keyHint || 'Paste your API key here...'}
                          value={tempApiKey}
                          onChange={(e) => setTempApiKey(e.target.value)}
                        />
                        <button
                          type="button"
                          className="model-byok-save-btn"
                          onClick={() => handleSaveKey(provider.id)}
                        >
                          Save
                        </button>
                        <button
                          type="button"
                          className="model-byok-cancel-btn"
                          onClick={() => setEditingKeyForProvider(null)}
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  )}

                  {/* Model Items */}
                  <div className="model-item-list">
                    {(() => {
                      const isExpanded = Boolean(expandedProviders[provider.id]) || Boolean(searchQuery.trim());
                      const visibleModels = isExpanded
                        ? provider.filteredModels
                        : provider.filteredModels.slice(0, 10);
                      const hasMore = provider.filteredModels.length > 10 && !isExpanded;

                      return (
                        <>
                          {visibleModels.map((modelName) => {
                            const isSelected = isCurrentProvider && activeModel === modelName;
                            const { label, badge } = getModelDescriptor(provider.id, modelName);

                            return (
                              <button
                                key={modelName}
                                type="button"
                                className={`model-option-card ${isSelected ? 'selected' : ''}`}
                                onClick={() => {
                                  onSelectModel(provider.id, modelName);
                                  onClose();
                                }}
                              >
                                <div className="model-option-main">
                                  <span className="model-name-text">{label}</span>
                                  <span className="model-technical-id">{modelName}</span>
                                </div>

                                <div className="model-option-meta">
                                  <span className="model-descriptor-tag">{badge}</span>
                                  {isSelected && (
                                    <div className="model-selected-check">
                                      <Check size={13} />
                                    </div>
                                  )}
                                </div>
                              </button>
                            );
                          })}

                          {hasMore && (
                            <button
                              type="button"
                              className="model-show-more-btn"
                              onClick={() => setExpandedProviders((prev) => ({ ...prev, [provider.id]: true }))}
                              style={{
                                padding: '8px',
                                background: 'var(--bg-canvas)',
                                border: '1px dashed var(--border-subtle)',
                                borderRadius: 'var(--radius-md)',
                                color: 'var(--primary)',
                                fontSize: '0.78rem',
                                fontWeight: 500,
                                cursor: 'pointer',
                                textAlign: 'center',
                                marginTop: '4px',
                              }}
                            >
                              + Show {provider.filteredModels.length - 10} more {provider.name} models...
                            </button>
                          )}
                        </>
                      );
                    })()}
                  </div>
                </div>
              );
            })
          )}
        </div>

        {/* Footer */}
        <div className="model-popover-footer">
          <div className="model-popover-footer-tip">
            <Radio size={12} color="var(--accent-cyan)" />
            <span>Multi-device isolated · Each tab retains its own active model & credentials</span>
          </div>

          <button
            type="button"
            className="model-popover-settings-link"
            onClick={() => {
              onClose();
              onOpenFullSettings();
            }}
          >
            <Settings size={13} />
            <span>Manage All Providers & Connectors</span>
          </button>
        </div>
      </motion.div>
    </motion.div>
  );
};
