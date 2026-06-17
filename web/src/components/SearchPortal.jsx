import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Search, ChevronRight } from 'lucide-react';
import { API_BASE } from '../config';

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
          ? `${API_BASE}/api/additives?q=${encodeURIComponent(query)}`
          : `${API_BASE}/api/additives`;
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
    <>
      {/* Full-width hero with search */}
      <section className="hero">
        <div className="container">
          <h1>查詢食品添加物，把關每一餐</h1>
          <p>即時查詢食品添加物、危害風險與社群意見反饋，開放社群參與修訂建議。</p>

          <div className="search-wrap">
            <input
              type="text"
              className="search-input"
              placeholder="搜尋中文名稱、英文名稱、INS 編號（例如：己二烯酸、Sorbic Acid、INS 200）..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
            <Search className="search-icon" size={24} />
          </div>

          <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', justifyContent: 'center', alignItems: 'center', marginTop: '24px' }}>
            <span style={{ fontSize: '0.9rem', color: 'var(--text-muted)' }}>常用分類：</span>
            {['防腐劑', '抗氧化劑', '甜味劑', '著色劑', '漂白劑'].map((cat) => (
              <button
                key={cat}
                onClick={() => setQuery(cat)}
                className={query === cat ? 'chip chip-active' : 'chip'}
              >
                {cat}
              </button>
            ))}
            {query && (
              <button onClick={() => setQuery('')} className="chip chip-clear">
                清除篩選
              </button>
            )}
          </div>
        </div>
      </section>

      {/* Results */}
      <section className="section">
        <div className="container">
          {loading && (
            <div style={{ textAlign: 'center', margin: '40px 0', color: 'var(--text-secondary)' }}>
              載入中...
            </div>
          )}

          {!loading && (
            <div className="card-grid">
              {results.map((item) => (
                <div
                  key={item.id}
                  className="card card-hover"
                  onClick={() => navigate(`/additive/${item.record_id}`)}
                  style={{
                    textAlign: 'left',
                    display: 'flex',
                    flexDirection: 'column',
                    justifyContent: 'space-between',
                    height: '100%'
                  }}
                >
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '12px' }}>
                      <span className="tag tag-orange">
                        {item.ins_or_e_number || '無 INS 編號'}
                      </span>

                      <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                        {Array.isArray(item.category) ? item.category.map((cat, idx) => (
                          <span key={idx} className="tag tag-green">
                            {cat}
                          </span>
                        )) : item.category && (
                          <span className="tag tag-green">
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
                    color: 'var(--off-orange-dark)',
                    fontWeight: '600',
                    fontSize: '0.9rem',
                    borderTop: '1px solid var(--border-color)',
                    paddingTop: '12px',
                    marginTop: 'auto'
                  }}>
                    查看詳細資訊
                    <ChevronRight size={16} style={{ marginLeft: '4px' }} />
                  </div>
                </div>
              ))}

              {!loading && results.length === 0 && (
                <div className="empty-state" style={{ gridColumn: '1 / -1' }}>
                  找不到相符的食品添加物。請嘗試其他關鍵字。
                </div>
              )}
            </div>
          )}
        </div>
      </section>
    </>
  );
}
