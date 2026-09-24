import { useState, useEffect, useCallback } from 'react';
import { Building2, Plus, Edit3, Trash2, AlertCircle, ExternalLink, RefreshCw, X } from 'lucide-react';
import { API_BASE } from '../config';

const SEVERITY = {
  1: { label: '標示違規', color: 'var(--warning)', tag: 'tag-amber' },
  2: { label: '成分違規', color: 'var(--off-orange)', tag: 'tag-orange' },
  3: { label: '重大事件', color: 'var(--danger)', tag: 'tag-red' },
};

const SOURCE_TYPES = [
  { value: 'official', label: '官方公告' },
  { value: 'news', label: '新聞媒體' },
  { value: 'social', label: '社群討論' },
  { value: 'consumer_complaint', label: '消費者投訴' },
  { value: 'wiki', label: '維基百科' },
  { value: 'manual', label: '人工新增' },
];

const emptyForm = (producerId) => ({
  producer_id: producerId || '',
  title: '',
  content: '',
  alert_date: '',
  source_url: '',
  source_type: 'news',
  severity: 1,
});

export default function SafetyEventsManager({ getAuthHeader, onAuthFail }) {
  const [producers, setProducers] = useState([]);
  const [selectedProducer, setSelectedProducer] = useState(null);
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  // modal: { mode: 'create' | 'edit', form }
  const [modal, setModal] = useState(null);
  const [saving, setSaving] = useState(false);

  const authFetch = useCallback(async (url, options = {}) => {
    const headers = { ...(options.headers || {}), ...getAuthHeader() };
    const res = await fetch(url, { ...options, headers });
    if (res.status === 401 || res.status === 403) {
      onAuthFail();
      throw new Error('auth');
    }
    return res;
  }, [getAuthHeader, onAuthFail]);

  const fetchProducers = useCallback(async () => {
    setError('');
    try {
      const res = await authFetch(`${API_BASE}/api/admin/producers`);
      if (res.ok) {
        const data = await res.json();
        setProducers(data);
        setSelectedProducer((prev) => prev ?? (data[0]?.id ?? null));
      } else {
        setError('無法取得廠商列表');
      }
    } catch (err) {
      if (err.message !== 'auth') setError('伺服器連線失敗');
    }
  }, [authFetch]);

  const fetchEvents = useCallback(async (producerId) => {
    if (!producerId) return;
    setLoading(true);
    setError('');
    try {
      const res = await authFetch(`${API_BASE}/api/admin/safety-events?producer_id=${producerId}`);
      if (res.ok) {
        setEvents(await res.json());
      } else {
        setError('無法取得事件列表');
      }
    } catch (err) {
      if (err.message !== 'auth') setError('伺服器連線失敗');
    } finally {
      setLoading(false);
    }
  }, [authFetch]);

  useEffect(() => { fetchProducers(); }, [fetchProducers]);
  useEffect(() => { if (selectedProducer) fetchEvents(selectedProducer); }, [selectedProducer, fetchEvents]);

  const handleDelete = async (id) => {
    if (!window.confirm('確定要刪除這筆食安事件嗎？此操作無法復原。')) return;
    try {
      const res = await authFetch(`${API_BASE}/api/admin/safety-events/${id}`, { method: 'DELETE' });
      if (res.ok) {
        await fetchEvents(selectedProducer);
        await fetchProducers();
      } else {
        alert('刪除失敗，請重試');
      }
    } catch (err) {
      if (err.message !== 'auth') alert('連線失敗');
    }
  };

  const handleSave = async (e) => {
    e.preventDefault();
    if (!modal.form.title.trim()) { alert('請填寫事件標題'); return; }
    if (!modal.form.producer_id) { alert('請選擇廠商'); return; }
    setSaving(true);
    try {
      const isEdit = modal.mode === 'edit';
      const url = isEdit
        ? `${API_BASE}/api/admin/safety-events/${modal.form.id}`
        : `${API_BASE}/api/admin/safety-events`;
      const body = {
        producer_id: Number(modal.form.producer_id),
        title: modal.form.title,
        content: modal.form.content,
        alert_date: modal.form.alert_date,
        source_url: modal.form.source_url,
        source_type: modal.form.source_type,
        severity: Number(modal.form.severity),
      };
      const res = await authFetch(url, {
        method: isEdit ? 'PUT' : 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        setModal(null);
        await fetchEvents(selectedProducer);
        await fetchProducers();
      } else {
        const d = await res.json().catch(() => ({}));
        alert(`儲存失敗: ${d.detail || '請重試'}`);
      }
    } catch (err) {
      if (err.message !== 'auth') alert('連線失敗');
    } finally {
      setSaving(false);
    }
  };

  const updateForm = (field, value) =>
    setModal((m) => ({ ...m, form: { ...m.form, [field]: value } }));

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 280px) 1fr', gap: '24px', alignItems: 'start' }}>
      {/* 左側：廠商列表 */}
      <div className="card" style={{ padding: '16px', textAlign: 'left' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
          <h3 style={{ margin: 0, fontSize: '1rem', display: 'flex', alignItems: 'center', gap: '6px' }}>
            <Building2 size={16} /> 廠商
          </h3>
          <button className="btn-ghost-sm" onClick={fetchProducers}><RefreshCw size={14} /></button>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          {producers.map((p) => (
            <button
              key={p.id}
              onClick={() => setSelectedProducer(p.id)}
              style={{
                display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                padding: '10px 12px', borderRadius: '8px', cursor: 'pointer',
                border: '1px solid var(--border-color)',
                background: selectedProducer === p.id ? 'var(--off-orange-soft)' : 'transparent',
                color: selectedProducer === p.id ? 'var(--off-orange-dark)' : 'var(--text-primary)',
                fontWeight: selectedProducer === p.id ? '600' : '400',
                fontFamily: 'var(--font-family)', fontSize: '0.9rem', textAlign: 'left',
              }}
            >
              <span>{p.name}</span>
              <span className="tag" style={{ fontSize: '0.75rem', padding: '2px 8px' }}>{p.event_count}</span>
            </button>
          ))}
          {producers.length === 0 && (
            <span style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>尚無廠商資料</span>
          )}
        </div>
      </div>

      {/* 右側：事件列表 */}
      <div style={{ textAlign: 'left' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <h3 style={{ margin: 0, fontSize: '1.2rem' }}>
            食安事件 ({events.length})
          </h3>
          <button
            className="btn btn-primary"
            onClick={() => setModal({ mode: 'create', form: emptyForm(selectedProducer) })}
            disabled={!selectedProducer}
          >
            <Plus size={16} /> 新增事件
          </button>
        </div>

        {error && (
          <div className="alert-error" style={{ padding: '12px 18px', marginBottom: '18px' }}>
            <AlertCircle size={18} /> <span>{error}</span>
          </div>
        )}

        {loading ? (
          <div style={{ padding: '40px', textAlign: 'center', color: 'var(--text-secondary)' }}>載入事件中...</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
            {events.map((ev) => {
              const sev = SEVERITY[ev.severity] || SEVERITY[1];
              return (
                <div key={ev.id} className="card" style={{ padding: '18px', textAlign: 'left' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px', flexWrap: 'wrap' }}>
                    <div style={{ flex: 1, minWidth: '240px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', marginBottom: '6px' }}>
                        <span className={`tag ${sev.tag}`} style={{ fontSize: '0.78rem' }}>{sev.label}</span>
                        <span className="tag" style={{ fontSize: '0.78rem' }}>{ev.alert_date || '日期不明'}</span>
                        <span className="tag tag-green" style={{ fontSize: '0.78rem' }}>
                          {SOURCE_TYPES.find((s) => s.value === ev.source_type)?.label || ev.source_type || '未分類'}
                        </span>
                      </div>
                      <h4 style={{ margin: '4px 0', fontSize: '1.05rem' }}>{ev.title}</h4>
                      <p style={{ fontSize: '0.9rem', color: 'var(--text-secondary)', lineHeight: '1.5', whiteSpace: 'pre-wrap' }}>
                        {ev.content || '（無摘要）'}
                      </p>
                      {ev.source_url && (
                        <a href={ev.source_url} target="_blank" rel="noopener noreferrer"
                           style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', fontSize: '0.82rem', color: 'var(--off-orange-dark)', marginTop: '8px' }}>
                          <ExternalLink size={13} /> 來源連結
                        </a>
                      )}
                    </div>
                    <div style={{ display: 'flex', gap: '8px' }}>
                      <button className="btn btn-secondary" style={{ padding: '8px 12px' }}
                        onClick={() => setModal({ mode: 'edit', form: { ...ev, alert_date: ev.alert_date || '', source_url: ev.source_url || '', content: ev.content || '' } })}>
                        <Edit3 size={14} /> 編輯
                      </button>
                      <button className="btn btn-danger-outline" style={{ padding: '8px 12px' }}
                        onClick={() => handleDelete(ev.id)}>
                        <Trash2 size={14} /> 刪除
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
            {events.length === 0 && (
              <div className="empty-state" style={{ padding: '60px 20px' }}>
                此廠商目前沒有食安事件紀錄。可點擊「新增事件」手動加入。
              </div>
            )}
          </div>
        )}
      </div>

      {/* 新增 / 編輯 Modal */}
      {modal && (
        <div className="modal-overlay">
          <div className="modal-content" style={{ textAlign: 'left' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
              <h2 style={{ fontSize: '1.4rem', margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
                {modal.mode === 'edit' ? <Edit3 size={20} /> : <Plus size={20} />}
                {modal.mode === 'edit' ? '編輯食安事件' : '新增食安事件'}
              </h2>
              <button className="btn-ghost-sm" onClick={() => setModal(null)}><X size={18} /></button>
            </div>

            <form onSubmit={handleSave}>
              <div className="form-group">
                <label className="form-label">所屬廠商</label>
                <select className="form-input" value={modal.form.producer_id}
                  onChange={(e) => updateForm('producer_id', e.target.value)} required>
                  <option value="">請選擇廠商</option>
                  {producers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </div>

              <div className="form-group">
                <label className="form-label">事件標題</label>
                <input type="text" className="form-input" value={modal.form.title}
                  onChange={(e) => updateForm('title', e.target.value)} required />
              </div>

              <div className="form-group">
                <label className="form-label">事件摘要</label>
                <textarea className="form-textarea" value={modal.form.content}
                  onChange={(e) => updateForm('content', e.target.value)} />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
                <div className="form-group">
                  <label className="form-label">事件日期 (YYYY-MM)</label>
                  <input type="text" className="form-input" placeholder="2025-08" value={modal.form.alert_date}
                    onChange={(e) => updateForm('alert_date', e.target.value)} />
                </div>
                <div className="form-group">
                  <label className="form-label">嚴重度</label>
                  <select className="form-input" value={modal.form.severity}
                    onChange={(e) => updateForm('severity', e.target.value)}>
                    <option value={1}>1 — 標示違規</option>
                    <option value={2}>2 — 成分違規</option>
                    <option value={3}>3 — 重大事件</option>
                  </select>
                </div>
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
                <div className="form-group">
                  <label className="form-label">來源類型</label>
                  <select className="form-input" value={modal.form.source_type}
                    onChange={(e) => updateForm('source_type', e.target.value)}>
                    {SOURCE_TYPES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
                  </select>
                </div>
                <div className="form-group">
                  <label className="form-label">來源連結 (URL)</label>
                  <input type="text" className="form-input" placeholder="https://..." value={modal.form.source_url}
                    onChange={(e) => updateForm('source_url', e.target.value)} />
                </div>
              </div>

              <div style={{ display: 'flex', gap: '12px', justifyContent: 'flex-end', marginTop: '20px' }}>
                <button type="button" className="btn btn-secondary" onClick={() => setModal(null)} disabled={saving}>取消</button>
                <button type="submit" className="btn btn-primary" disabled={saving}>
                  {saving ? '儲存中...' : (modal.mode === 'edit' ? '更新事件' : '新增事件')}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
