import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Lock, User, ArrowLeft, AlertCircle } from 'lucide-react';
import { API_BASE } from '../config';

export default function AdminLogin() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const navigate = useNavigate();

  const handleLogin = async (e) => {
    e.preventDefault();
    if (!username.trim() || !password.trim()) return;

    setLoading(true);
    setError('');

    try {
      const response = await fetch(`${API_BASE}/api/admin/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
      });

      if (response.ok) {
        const data = await response.json();
        localStorage.setItem('admin_token', data.access_token);
        navigate('/admin/dashboard');
      } else {
        const errData = await response.json();
        setError(errData.detail || '帳號或密碼錯誤');
      }
    } catch (err) {
      console.error('Login error:', err);
      setError('伺服器連線失敗，請檢查後端是否啟動');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      minHeight: 'calc(100vh - 200px)',
      padding: '40px 20px',
      width: '100%'
    }}>
      <div className="card" style={{
        width: '100%',
        maxWidth: '420px',
        padding: '40px 30px',
        position: 'relative'
      }}>
        {/* Back Arrow */}
        <button
          onClick={() => navigate('/')}
          style={{
            position: 'absolute',
            top: '24px',
            left: '24px',
            background: 'none',
            border: 'none',
            color: 'var(--text-secondary)',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
            fontSize: '0.9rem',
            fontFamily: 'var(--font-family)',
            transition: 'var(--transition-smooth)'
          }}
          onMouseEnter={(e) => e.target.style.color = 'var(--off-orange-dark)'}
          onMouseLeave={(e) => e.target.style.color = 'var(--text-secondary)'}
        >
          <ArrowLeft size={16} />
          返回主頁
        </button>

        <div style={{ textAlign: 'center', marginTop: '20px', marginBottom: '32px' }}>
          <div style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            width: '60px',
            height: '60px',
            borderRadius: '50%',
            background: 'var(--off-orange-soft)',
            border: '1px solid rgba(255, 135, 20, 0.35)',
            color: 'var(--off-orange)',
            marginBottom: '16px'
          }}>
            <Lock size={28} />
          </div>
          <h2 style={{ fontSize: '1.6rem', marginBottom: '8px' }}>管理員登入</h2>
          <p style={{ color: 'var(--text-secondary)', fontSize: '0.9rem' }}>請輸入食品添加物審閱系統管理密鑰</p>
        </div>

        {error && (
          <div className="alert-error" style={{ marginBottom: '24px' }}>
            <AlertCircle size={18} style={{ flexShrink: 0 }} />
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleLogin}>
          <div className="form-group" style={{ position: 'relative' }}>
            <label className="form-label">管理員帳號</label>
            <div style={{ position: 'relative' }}>
              <input
                type="text"
                className="form-input"
                placeholder="請輸入帳號"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                style={{ paddingLeft: '44px' }}
                required
              />
              <User size={18} style={{
                position: 'absolute',
                left: '14px',
                top: '50%',
                transform: 'translateY(-50%)',
                color: 'var(--text-muted)'
              }} />
            </div>
          </div>

          <div className="form-group" style={{ position: 'relative', marginBottom: '32px' }}>
            <label className="form-label">審查密碼</label>
            <div style={{ position: 'relative' }}>
              <input
                type="password"
                className="form-input"
                placeholder="請輸入密碼"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                style={{ paddingLeft: '44px' }}
                required
              />
              <Lock size={18} style={{
                position: 'absolute',
                left: '14px',
                top: '50%',
                transform: 'translateY(-50%)',
                color: 'var(--text-muted)'
              }} />
            </div>
          </div>

          <button
            type="submit"
            className="btn btn-primary"
            style={{ width: '100%', justifyContent: 'center', padding: '12px' }}
            disabled={loading}
          >
            {loading ? '驗證中...' : '進行身分認證'}
          </button>
        </form>
      </div>
    </div>
  );
}
