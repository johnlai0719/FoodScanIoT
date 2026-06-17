import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { jwtDecode } from 'jwt-decode';
import { LogOut, Check, X, Shield, Clock, User, AlertCircle, Info, RefreshCw, FileText, AlertTriangle } from 'lucide-react';
import { API_BASE } from '../config';
import SafetyEventsManager from './SafetyEventsManager';

const getAuthHeader = () => {
  const token = localStorage.getItem('admin_token');
  if (!token) return {};
  return { 'Authorization': `Bearer ${token}` };
};

function SuggestionsPanel({ onAuthFail }) {
  const [suggestions, setSuggestions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const fetchSuggestions = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await fetch(`${API_BASE}/api/admin/suggestions?status=pending`, { headers: getAuthHeader() });
      if (response.status === 401 || response.status === 403) { onAuthFail(); return; }
      if (response.ok) {
        setSuggestions(await response.json());
      } else {
        setError('無法取得審核列表資料');
      }
    } catch (err) {
      console.error('Fetch suggestions error:', err);
      setError('伺服器連線失敗，請檢查後端服務是否正常運作');
    } finally {
      setLoading(false);
    }
  }, [onAuthFail]);

  useEffect(() => { fetchSuggestions(); }, [fetchSuggestions]);

  const handleAction = async (id, action) => {
    try {
      const response = await fetch(`${API_BASE}/api/admin/suggestions/${id}/${action}`, {
        method: 'PUT', headers: getAuthHeader(),
      });
      if (response.status === 401 || response.status === 403) { onAuthFail(); return; }
      if (response.ok) {
        await fetchSuggestions();
      } else {
        const data = await response.json().catch(() => ({}));
        alert(`操作失敗: ${data.detail || '請稍後再試'}`);
      }
    } catch (err) {
      console.error(`Action ${action} error:`, err);
      alert('連線失敗，請重試');
    }
  };

  const formatFieldName = (field) => ({
    name_zh: '中文名稱', name_en: '英文名稱', ins_or_e_number: 'INS 編號',
    adi: '每日容許量 (ADI)', description: '描述與說明',
  }[field] || field);

  if (loading) {
    return <div style={{ padding: '60px 20px', textAlign: 'center', color: 'var(--text-secondary)' }}>載入審核清單中...</div>;
  }

  return (
    <div>
      {error && (
        <div className="alert-error" style={{ padding: '16px 24px', marginBottom: '24px' }}>
          <AlertCircle size={20} /><span>{error}</span>
        </div>
      )}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
        <h3 style={{ fontSize: '1.2rem', margin: 0 }}>待處理的修改建議 ({suggestions.length})</h3>
        <button className="btn btn-secondary" onClick={fetchSuggestions} style={{ padding: '8px 12px' }}>
          <RefreshCw size={14} /> 重新整理
        </button>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
        {suggestions.map((sug) => (
          <div key={sug.id} className="card" style={{ textAlign: 'left', padding: '24px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '12px', borderBottom: '1px solid var(--border-color)', paddingBottom: '14px', marginBottom: '20px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
                <span className="tag tag-orange" style={{ fontSize: '0.8rem' }}>添加物 ID: {sug.record_id}</span>
                <span className="tag tag-green" style={{ fontSize: '0.8rem' }}>修改欄位: {formatFieldName(sug.field_name)}</span>
              </div>
              <div style={{ display: 'flex', gap: '16px', color: 'var(--text-muted)', fontSize: '0.85rem', flexWrap: 'wrap' }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}><User size={14} /> 提案者: {sug.suggested_by || '匿名'}</span>
                <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}><Clock size={14} /> {new Date(sug.created_at).toLocaleString('zh-TW')}</span>
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '20px', marginBottom: '20px' }}>
              <div className="panel panel-danger">
                <span className="tag tag-red" style={{ marginBottom: '10px' }}>歷史舊值 (Old Value)</span>
                <div style={{ fontSize: '0.95rem', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', maxHeight: '200px', overflowY: 'auto', lineHeight: '1.6', marginTop: '10px' }}>
                  {sug.old_value !== null ? sug.old_value : <em style={{ color: 'var(--text-muted)' }}>無舊資料</em>}
                </div>
              </div>
              <div className="panel panel-success">
                <span className="tag tag-green" style={{ marginBottom: '10px' }}>建議新值 (New Value)</span>
                <div style={{ fontSize: '0.95rem', color: 'var(--text-primary)', whiteSpace: 'pre-wrap', maxHeight: '200px', overflowY: 'auto', lineHeight: '1.6', fontWeight: '550', marginTop: '10px' }}>
                  {sug.new_value}
                </div>
              </div>
            </div>

            <div className="panel" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end', flexWrap: 'wrap', gap: '20px' }}>
              <div style={{ flex: '1', minWidth: '250px' }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '6px' }}>
                  <Info size={14} /> 提案理由 / 文獻佐證：
                </span>
                <p style={{ fontSize: '0.9rem', color: 'var(--text-secondary)', lineHeight: '1.5' }}>{sug.reason || '無提供理由。'}</p>
              </div>
              <div style={{ display: 'flex', gap: '12px' }}>
                <button className="btn btn-danger-outline" onClick={() => handleAction(sug.id, 'reject')}><X size={16} /> 拒絕建議</button>
                <button className="btn btn-success" onClick={() => handleAction(sug.id, 'approve')}><Check size={16} /> 核准並更新</button>
              </div>
            </div>
          </div>
        ))}
        {suggestions.length === 0 && (
          <div className="empty-state" style={{ padding: '80px 20px' }}>目前沒有待處理的審閱建議。</div>
        )}
      </div>
    </div>
  );
}

export default function AdminDashboard() {
  const [tab, setTab] = useState('suggestions');
  const navigate = useNavigate();

  const handleAuthFail = useCallback(() => {
    localStorage.removeItem('admin_token');
    navigate('/admin/login');
  }, [navigate]);

  const adminUser = (() => {
    const token = localStorage.getItem('admin_token');
    if (!token) return '管理員';
    try { return jwtDecode(token).username || '管理員'; } catch { return '管理員'; }
  })();

  useEffect(() => {
    const token = localStorage.getItem('admin_token');
    if (!token) { navigate('/admin/login'); return; }
    try { jwtDecode(token); } catch { handleAuthFail(); }
  }, [navigate, handleAuthFail]);

  const handleLogout = () => {
    localStorage.removeItem('admin_token');
    navigate('/');
  };

  const tabBtn = (key, label, Icon) => (
    <button
      onClick={() => setTab(key)}
      style={{
        display: 'flex', alignItems: 'center', gap: '8px',
        padding: '12px 20px', cursor: 'pointer', fontFamily: 'var(--font-family)',
        fontSize: '0.95rem', fontWeight: tab === key ? '600' : '400',
        background: 'none', border: 'none',
        color: tab === key ? 'var(--off-orange-dark)' : 'var(--text-secondary)',
        borderBottom: tab === key ? '3px solid var(--off-orange)' : '3px solid transparent',
        marginBottom: '-1px',
      }}
    >
      <Icon size={18} /> {label}
    </button>
  );

  return (
    <div className="container section">
      <div className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px', padding: '20px 30px', flexWrap: 'wrap', gap: '20px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px', textAlign: 'left' }}>
          <div style={{ background: 'var(--off-orange-soft)', border: '1px solid rgba(255, 135, 20, 0.35)', color: 'var(--off-orange)', padding: '12px', borderRadius: '12px' }}>
            <Shield size={28} />
          </div>
          <div>
            <h1 style={{ margin: 0, fontSize: '1.8rem' }}>審核控制面板</h1>
            <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginTop: '4px' }}>
              當前登入：<strong style={{ color: 'var(--off-orange-dark)' }}>{adminUser}</strong>
            </p>
          </div>
        </div>
        <button className="btn btn-danger" onClick={handleLogout}><LogOut size={16} /> 登出系統</button>
      </div>

      {/* Tab bar */}
      <div style={{ display: 'flex', gap: '8px', borderBottom: '1px solid var(--border-color)', marginBottom: '28px' }}>
        {tabBtn('suggestions', '添加物修改建議', FileText)}
        {tabBtn('events', '廠商食安事件管理', AlertTriangle)}
      </div>

      {tab === 'suggestions' && <SuggestionsPanel onAuthFail={handleAuthFail} />}
      {tab === 'events' && <SafetyEventsManager getAuthHeader={getAuthHeader} onAuthFail={handleAuthFail} />}
    </div>
  );
}
