import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { jwtDecode } from 'jwt-decode';
import { LogOut, Check, X, Shield, Clock, User, AlertCircle, Info, RefreshCw } from 'lucide-react';

const getAuthHeader = () => {
  const token = localStorage.getItem('admin_token');
  if (!token) return null;
  return { 'Authorization': `Bearer ${token}` };
};

export default function AdminDashboard() {
  const [suggestions, setSuggestions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const navigate = useNavigate();

  const adminUser = (() => {
    const token = localStorage.getItem('admin_token');
    if (!token) return '管理員';
    try {
      const decoded = jwtDecode(token);
      return decoded.username || '管理員';
    } catch {
      return '管理員';
    }
  })();

  const fetchSuggestions = useCallback(async () => {
    setLoading(true);
    setError('');
    const headers = getAuthHeader();
    if (!headers) {
      navigate('/admin/login');
      return;
    }

    try {
      const response = await fetch('http://127.0.0.1:8000/api/admin/suggestions?status=pending', {
        headers
      });

      if (response.status === 401) {
        localStorage.removeItem('admin_token');
        navigate('/admin/login');
        return;
      }

      if (response.ok) {
        const data = await response.json();
        setSuggestions(data);
      } else {
        setError('無法取得審核列表資料');
      }
    } catch (err) {
      console.error('Fetch suggestions error:', err);
      setError('伺服器連線失敗，請檢查後端服務是否正常運作');
    } finally {
      setLoading(false);
    }
  }, [navigate]);

  useEffect(() => {
    const token = localStorage.getItem('admin_token');
    if (!token) {
      navigate('/admin/login');
      return;
    }

    try {
      jwtDecode(token);
    } catch {
      localStorage.removeItem('admin_token');
      navigate('/admin/login');
      return;
    }

    fetchSuggestions();
  }, [fetchSuggestions, navigate]);

  const handleAction = async (id, action) => {
    const headers = getAuthHeader();
    if (!headers) {
      navigate('/admin/login');
      return;
    }

    try {
      const response = await fetch(`http://127.0.0.1:8000/api/admin/suggestions/${id}/${action}`, {
        method: 'PUT',
        headers
      });

      if (response.ok) {
        await fetchSuggestions();
      } else {
        const data = await response.json();
        alert(`操作失敗: ${data.detail || '請稍後再試'}`);
      }
    } catch (err) {
      console.error(`Action ${action} error:`, err);
      alert('連線失敗，請重試');
    }
  };

  const handleLogout = () => {
    localStorage.removeItem('admin_token');
    navigate('/');
  };

  const formatFieldName = (field) => {
    const names = {
      name_zh: '中文名稱',
      name_en: '英文名稱',
      ins_or_e_number: 'INS 編號',
      adi: '每日容許量 (ADI)',
      description: '描述與說明'
    };
    return names[field] || field;
  };

  return (
    <div style={{ padding: '40px 20px', maxWidth: '1200px', margin: '0 auto', width: '100%' }}>
      {/* Dashboard Header */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        marginBottom: '40px',
        background: 'rgba(30, 41, 59, 0.4)',
        padding: '20px 30px',
        borderRadius: '16px',
        border: '1px solid rgba(255, 255, 255, 0.05)',
        backdropFilter: 'blur(8px)',
        flexWrap: 'wrap',
        gap: '20px'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px', textAlign: 'left' }}>
          <div style={{
            background: 'rgba(16, 185, 129, 0.1)',
            border: '1px solid rgba(16, 185, 129, 0.3)',
            color: '#10b981',
            padding: '12px',
            borderRadius: '12px'
          }}>
            <Shield size={28} />
          </div>
          <div>
            <h1 style={{ margin: 0, fontSize: '1.8rem' }}>審核控制面板</h1>
            <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem', marginTop: '4px' }}>
              當前登入：<strong style={{ color: '#10b981' }}>{adminUser}</strong>
            </p>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '12px' }}>
          <button className="btn btn-secondary" onClick={fetchSuggestions} style={{ padding: '10px 14px' }}>
            <RefreshCw size={16} />
            整理列表
          </button>
          <button className="btn btn-danger" onClick={handleLogout}>
            <LogOut size={16} />
            登出系統
          </button>
        </div>
      </div>

      {error && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: '10px',
          background: 'rgba(239, 68, 68, 0.1)',
          border: '1px solid rgba(239, 68, 68, 0.25)',
          borderRadius: '12px',
          padding: '16px 24px',
          color: '#f87171',
          marginBottom: '30px',
          textAlign: 'left'
        }}>
          <AlertCircle size={20} />
          <span>{error}</span>
        </div>
      )}

      {loading ? (
        <div style={{ padding: '60px 20px', textAlign: 'center', color: 'var(--text-secondary)' }}>
          載入審核清單中...
        </div>
      ) : (
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
            <h2 style={{ fontSize: '1.3rem', margin: 0 }}>
              待處理的修改建議 ({suggestions.length})
            </h2>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
            {suggestions.map((sug) => (
              <div 
                key={sug.id} 
                className="glass-card" 
                style={{ 
                  textAlign: 'left',
                  border: '1px solid rgba(255, 255, 255, 0.08)',
                  padding: '24px'
                }}
              >
                {/* Meta details */}
                <div style={{ 
                  display: 'flex', 
                  justifyContent: 'space-between', 
                  alignItems: 'flex-start',
                  flexWrap: 'wrap',
                  gap: '12px',
                  borderBottom: '1px solid rgba(255, 255, 255, 0.06)',
                  paddingBottom: '14px',
                  marginBottom: '20px'
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
                    <span style={{
                      background: 'rgba(59, 130, 246, 0.15)',
                      color: '#3b82f6',
                      border: '1px solid rgba(59, 130, 246, 0.3)',
                      fontSize: '0.8rem',
                      padding: '4px 10px',
                      borderRadius: '6px',
                      fontWeight: 'bold'
                    }}>
                      添加物 ID: {sug.record_id}
                    </span>
                    
                    <span style={{
                      background: 'rgba(16, 185, 129, 0.15)',
                      color: '#10b981',
                      border: '1px solid rgba(16, 185, 129, 0.3)',
                      fontSize: '0.8rem',
                      padding: '4px 10px',
                      borderRadius: '6px',
                      fontWeight: 'bold'
                    }}>
                      修改欄位: {formatFieldName(sug.field_name)}
                    </span>
                  </div>

                  <div style={{ display: 'flex', gap: '16px', color: 'var(--text-muted)', fontSize: '0.85rem' }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                      <User size={14} />
                      提案者: {sug.suggested_by || '匿名'}
                    </span>
                    <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                      <Clock size={14} />
                      時間: {new Date(sug.created_at).toLocaleString('zh-TW')}
                    </span>
                  </div>
                </div>

                {/* Left/Right Diff Side-by-side View */}
                <div style={{ 
                  display: 'grid', 
                  gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', 
                  gap: '20px',
                  marginBottom: '20px'
                }}>
                  {/* Left Side: Old Value */}
                  <div style={{
                    background: 'rgba(239, 68, 68, 0.08)',
                    border: '1px solid rgba(239, 68, 68, 0.2)',
                    borderRadius: '12px',
                    padding: '16px'
                  }}>
                    <span style={{
                      display: 'inline-block',
                      fontSize: '0.75rem',
                      fontWeight: 'bold',
                      color: '#ef4444',
                      background: 'rgba(239, 68, 68, 0.15)',
                      padding: '2px 8px',
                      borderRadius: '4px',
                      marginBottom: '10px'
                    }}>
                      歷史舊值 (Old Value)
                    </span>
                    <div style={{ 
                      fontSize: '0.95rem', 
                      color: 'var(--text-secondary)',
                      whiteSpace: 'pre-wrap',
                      maxHeight: '200px',
                      overflowY: 'auto',
                      lineHeight: '1.6'
                    }}>
                      {sug.old_value !== null ? sug.old_value : <em style={{ color: 'var(--text-muted)' }}>無舊資料</em>}
                    </div>
                  </div>

                  {/* Right Side: New Value */}
                  <div style={{
                    background: 'rgba(16, 185, 129, 0.08)',
                    border: '1px solid rgba(16, 185, 129, 0.2)',
                    borderRadius: '12px',
                    padding: '16px'
                  }}>
                    <span style={{
                      display: 'inline-block',
                      fontSize: '0.75rem',
                      fontWeight: 'bold',
                      color: '#10b981',
                      background: 'rgba(16, 185, 129, 0.15)',
                      padding: '2px 8px',
                      borderRadius: '4px',
                      marginBottom: '10px'
                    }}>
                      建議新值 (New Value)
                    </span>
                    <div style={{ 
                      fontSize: '0.95rem', 
                      color: 'var(--text-primary)',
                      whiteSpace: 'pre-wrap',
                      maxHeight: '200px',
                      overflowY: 'auto',
                      lineHeight: '1.6',
                      fontWeight: '550'
                    }}>
                      {sug.new_value}
                    </div>
                  </div>
                </div>

                {/* Reason & Action Buttons */}
                <div style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'flex-end',
                  flexWrap: 'wrap',
                  gap: '20px',
                  background: 'rgba(15, 23, 42, 0.3)',
                  padding: '16px',
                  borderRadius: '10px',
                  border: '1px solid rgba(255, 255, 255, 0.04)'
                }}>
                  <div style={{ flex: '1', minWidth: '250px' }}>
                    <span style={{ 
                      display: 'flex', 
                      alignItems: 'center', 
                      gap: '6px', 
                      fontSize: '0.85rem', 
                      color: 'var(--text-muted)',
                      marginBottom: '6px'
                    }}>
                      <Info size={14} />
                      提案理由 / 文獻佐證：
                    </span>
                    <p style={{ fontSize: '0.9rem', color: 'var(--text-secondary)', lineHeight: '1.5' }}>
                      {sug.reason || '無提供理由。'}
                    </p>
                  </div>

                  <div style={{ display: 'flex', gap: '12px' }}>
                    <button 
                      className="btn btn-secondary" 
                      onClick={() => handleAction(sug.id, 'reject')}
                      style={{ 
                        borderColor: 'rgba(239, 68, 68, 0.4)',
                        color: '#f87171',
                        background: 'rgba(239, 68, 68, 0.05)'
                      }}
                    >
                      <X size={16} />
                      拒絕建議
                    </button>
                    <button 
                      className="btn btn-primary" 
                      onClick={() => handleAction(sug.id, 'approve')}
                      style={{
                        background: 'var(--success-gradient)'
                      }}
                    >
                      <Check size={16} />
                      核准並更新
                    </button>
                  </div>
                </div>
              </div>
            ))}

            {suggestions.length === 0 && (
              <div style={{
                textAlign: 'center',
                padding: '80px 20px',
                color: 'var(--text-secondary)',
                background: 'var(--bg-card)',
                border: '1px dashed rgba(255, 255, 255, 0.1)',
                borderRadius: '16px'
              }}>
                目前沒有待處理的審閱建議。
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
