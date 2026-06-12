import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, ChevronRight, Shield } from 'lucide-react';

export default function SearchPortal() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    const fetchAdditives = async () => {
      setLoading(true);
      try {
        const url = query 
          ? `http://127.0.0.1:8000/api/additives?q=${encodeURIComponent(query)}`
          : 'http://127.0.0.1:8000/api/additives';
        const response = await fetch(url);
        if (response.ok) {
          const data = await response.json();
          setResults(data);
        }
      } catch (error) {
        console.error('Failed to fetch additives:', error);
      } finally {
        setLoading(false);
      }
    };

    const debounceTimer = setTimeout(() => {
      fetchAdditives();
    }, 300);

    return () => clearTimeout(debounceTimer);
  }, [query]);

  return (
    <div style={{ padding: '40px 20px', maxWidth: '1200px', margin: '0 auto', width: '100%' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '40px' }}>
        <div>
          <h1>食品添加物社群審閱系統</h1>
          <p style={{ color: 'var(--text-secondary)', marginTop: '8px' }}>即時查詢食品添加物、危害風險與社群意見反饋</p>
        </div>
        <button 
          className="btn btn-secondary" 
          onClick={() => navigate('/admin/login')}
          style={{ display: 'flex', alignItems: 'center', gap: '8px' }}
        >
          <Shield size={18} />
          管理員登入
        </button>
      </div>

      <div style={{ position: 'relative', marginBottom: '40px' }}>
        <input
          type="text"
          className="form-input"
          placeholder="搜尋中文名稱、英文名稱、INS 編號（例如：己二烯酸、Sorbic Acid、INS 200）..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{
            padding: '18px 20px 18px 56px',
            fontSize: '1.2rem',
            borderRadius: '16px',
            background: 'rgba(30, 41, 59, 0.4)',
            backdropFilter: 'blur(8px)',
            border: '1px solid rgba(255, 255, 255, 0.1)',
            width: '100%',
            boxShadow: '0 8px 32px 0 rgba(0, 0, 0, 0.2)'
          }}
        />
        <Search 
          style={{
            position: 'absolute',
            left: '20px',
            top: '50%',
            transform: 'translateY(-50%)',
            color: 'var(--text-secondary)'
          }}
          size={24}
        />
      </div>

      <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', marginBottom: '30px', alignItems: 'center' }}>
        <span style={{ fontSize: '0.9rem', color: 'var(--text-muted)' }}>常用分類：</span>
        {['防腐劑', '抗氧化劑', '甜味劑', '著色劑', '漂白劑'].map((cat) => (
          <button
            key={cat}
            onClick={() => setQuery(cat)}
            style={{
              padding: '6px 12px',
              borderRadius: '20px',
              border: '1px solid rgba(255, 255, 255, 0.1)',
              background: query === cat ? 'var(--primary-gradient)' : 'rgba(255, 255, 255, 0.05)',
              color: '#fff',
              fontSize: '0.85rem',
              cursor: 'pointer',
              transition: 'var(--transition-smooth)'
            }}
          >
            {cat}
          </button>
        ))}
        {query && (
          <button
            onClick={() => setQuery('')}
            style={{
              padding: '6px 12px',
              borderRadius: '20px',
              border: '1px solid rgba(239, 68, 68, 0.3)',
              background: 'rgba(239, 68, 68, 0.1)',
              color: '#ef4444',
              fontSize: '0.85rem',
              cursor: 'pointer'
            }}
          >
            清除篩選
          </button>
        )}
      </div>

      {loading && (
        <div style={{ textAlign: 'center', margin: '40px 0', color: 'var(--text-secondary)' }}>
          載入中...
        </div>
      )}

      {!loading && (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))',
          gap: '24px'
        }}>
          {results.map((item) => (
            <div 
              key={item.id} 
              className="glass-card" 
              onClick={() => navigate(`/additive/${item.record_id}`)}
              style={{
                cursor: 'pointer',
                textAlign: 'left',
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'space-between',
                height: '100%',
                position: 'relative'
              }}
            >
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '12px' }}>
                  <span style={{
                    fontSize: '0.75rem',
                    fontWeight: 'bold',
                    padding: '4px 8px',
                    borderRadius: '6px',
                    background: 'rgba(59, 130, 246, 0.15)',
                    color: '#3b82f6',
                    border: '1px solid rgba(59, 130, 246, 0.3)'
                  }}>
                    {item.ins_or_e_number || '無 INS 編號'}
                  </span>
                  
                  <div style={{ display: 'flex', gap: '6px' }}>
                    {Array.isArray(item.category) ? item.category.map((cat, idx) => (
                      <span key={idx} style={{
                        fontSize: '0.75rem',
                        padding: '4px 8px',
                        borderRadius: '6px',
                        background: 'rgba(16, 185, 129, 0.15)',
                        color: '#10b981',
                        border: '1px solid rgba(16, 185, 129, 0.3)'
                      }}>
                        {cat}
                      </span>
                    )) : item.category && (
                      <span style={{
                        fontSize: '0.75rem',
                        padding: '4px 8px',
                        borderRadius: '6px',
                        background: 'rgba(16, 185, 129, 0.15)',
                        color: '#10b981',
                        border: '1px solid rgba(16, 185, 129, 0.3)'
                      }}>
                        {item.category}
                      </span>
                    )}
                  </div>
                </div>

                <h3 style={{ fontSize: '1.4rem', marginBottom: '4px' }}>{item.name_zh}</h3>
                <p style={{ fontSize: '0.9rem', color: 'var(--text-muted)', marginBottom: '16px' }}>{item.name_en}</p>
                
                <p style={{
                  fontSize: '0.9rem',
                  color: 'var(--text-secondary)',
                  display: '-webkit-box',
                  WebkitLineClamp: 3,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden',
                  marginBottom: '20px',
                  lineHeight: '1.5'
                }}>
                  {item.description || '暫無描述資料。歡迎提交修改建議。'}
                </p>
              </div>

              <div style={{
                display: 'flex',
                alignItems: 'center',
                color: '#3b82f6',
                fontWeight: '600',
                fontSize: '0.9rem',
                borderTop: '1px solid rgba(255, 255, 255, 0.05)',
                paddingTop: '12px',
                marginTop: 'auto'
              }}>
                查看詳細資訊
                <ChevronRight size={16} style={{ marginLeft: '4px' }} />
              </div>
            </div>
          ))}

          {!loading && results.length === 0 && (
            <div style={{
              gridColumn: '1 / -1',
              textAlign: 'center',
              padding: '60px 20px',
              color: 'var(--text-secondary)',
              background: 'var(--bg-card)',
              border: '1px dashed rgba(255, 255, 255, 0.1)',
              borderRadius: '16px'
            }}>
              找不到相符的食品添加物。請嘗試其他關鍵字。
            </div>
          )}
        </div>
      )}
    </div>
  );
}
