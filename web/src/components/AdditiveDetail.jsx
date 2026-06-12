import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, MessageSquare, Edit3, Send, AlertTriangle, Info, ShieldAlert } from 'lucide-react';

export default function AdditiveDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [additive, setAdditive] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Comment states
  const [commentAuthor, setCommentAuthor] = useState('');
  const [commentContent, setCommentContent] = useState('');
  const [submittingComment, setSubmittingComment] = useState(false);

  // Modal states
  const [showSuggestModal, setShowSuggestModal] = useState(false);
  const [selectedField, setSelectedField] = useState('');
  const [selectedFieldNameZh, setSelectedFieldNameZh] = useState('');
  const [suggestNewValue, setSuggestNewValue] = useState('');
  const [suggestName, setSuggestName] = useState('');
  const [suggestReason, setSuggestReason] = useState('');
  const [submittingSuggest, setSubmittingSuggest] = useState(false);

  const fetchAdditiveDetails = useCallback(async () => {
    try {
      const response = await fetch(`http://127.0.0.1:8000/api/additives/${id}`);
      if (!response.ok) {
        throw new Error('找不到該添加物資料');
      }
      const data = await response.json();
      setAdditive(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    Promise.resolve().then(() => {
      fetchAdditiveDetails();
    });
  }, [fetchAdditiveDetails]);

  const handleCommentSubmit = async (e) => {
    e.preventDefault();
    if (!commentAuthor.trim() || !commentContent.trim()) return;

    setSubmittingComment(true);
    try {
      const response = await fetch(`http://127.0.0.1:8000/api/additives/${id}/comment`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          author: commentAuthor,
          content: commentContent
        })
      });
      if (response.ok) {
        setCommentContent('');
        await fetchAdditiveDetails(); // Re-fetch to display the new comment
      } else {
        alert('提交留言失敗，請重試。');
      }
    } catch (err) {
      console.error('Comment submit error:', err);
    } finally {
      setSubmittingComment(false);
    }
  };

  const openSuggestModal = (fieldName, fieldNameZh, currentValue) => {
    setSelectedField(fieldName);
    setSelectedFieldNameZh(fieldNameZh);
    setSuggestNewValue(currentValue || '');
    setSuggestName('');
    setSuggestReason('');
    setShowSuggestModal(true);
  };

  const handleSuggestSubmit = async (e) => {
    e.preventDefault();
    if (!suggestNewValue.trim() || !suggestName.trim() || !suggestReason.trim()) {
      alert('請填寫所有欄位');
      return;
    }

    setSubmittingSuggest(true);
    try {
      const response = await fetch(`http://127.0.0.1:8000/api/additives/${id}/suggest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          field_name: selectedField,
          new_value: suggestNewValue,
          suggested_by: suggestName,
          reason: suggestReason
        })
      });

      if (response.ok) {
        alert('修改建議已成功提交，等待管理員審核。');
        setShowSuggestModal(false);
      } else {
        const errorData = await response.json();
        alert(`提交失敗: ${errorData.detail || '請重試'}`);
      }
    } catch (err) {
      console.error('Suggestion submit error:', err);
      alert('提交發生錯誤，請重試。');
    } finally {
      setSubmittingSuggest(false);
    }
  };

  if (loading) {
    return (
      <div className="container" style={{ padding: '60px 32px', textAlign: 'center', color: 'var(--text-secondary)' }}>
        載入中...
      </div>
    );
  }

  if (error || !additive) {
    return (
      <div style={{ padding: '60px 20px', maxWidth: '600px', margin: '0 auto', textAlign: 'center' }}>
        <div className="card" style={{ borderColor: 'rgba(214, 69, 69, 0.4)' }}>
          <AlertTriangle size={48} style={{ color: 'var(--danger)', marginBottom: '16px' }} />
          <h2>載入失敗</h2>
          <p style={{ marginBottom: '24px' }}>{error || '無法取得食品添加物資料。'}</p>
          <button className="btn btn-secondary" onClick={() => navigate('/')}>
            <ArrowLeft size={16} />
            返回搜尋主頁
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="container section">
      {/* Navigation */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '24px' }}>
        <button className="btn btn-secondary" onClick={() => navigate('/')}>
          <ArrowLeft size={16} />
          返回搜尋
        </button>
        <span style={{ fontSize: '0.9rem', color: 'var(--text-muted)' }}>
          系統編號: {additive.record_id}
        </span>
      </div>

      {/* Main Detail Card */}
      <div className="card" style={{ textAlign: 'left', marginBottom: '32px', padding: '32px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '16px', borderBottom: '1px solid var(--border-color)', paddingBottom: '24px', marginBottom: '24px' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '8px', flexWrap: 'wrap' }}>
              <h1 style={{ margin: 0, fontSize: '2.2rem' }}>{additive.name_zh}</h1>
              <button
                className="btn-ghost-sm"
                onClick={() => openSuggestModal('name_zh', '中文名稱', additive.name_zh)}
              >
                <Edit3 size={12} />
                建議修改
              </button>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '16px', flexWrap: 'wrap' }}>
              <span style={{ fontSize: '1.2rem', color: 'var(--text-secondary)' }}>{additive.name_en}</span>
              <button
                className="btn-ghost-sm"
                onClick={() => openSuggestModal('name_en', '英文名稱', additive.name_en)}
              >
                <Edit3 size={12} />
                建議修改
              </button>
            </div>

            <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
              {Array.isArray(additive.category) ? additive.category.map((cat, idx) => (
                <span key={idx} className="tag tag-green" style={{ borderRadius: '999px', padding: '6px 12px' }}>
                  {cat}
                </span>
              )) : additive.category && (
                <span className="tag tag-green" style={{ borderRadius: '999px', padding: '6px 12px' }}>
                  {additive.category}
                </span>
              )}
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', alignItems: 'flex-end' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span className="tag tag-orange" style={{ fontSize: '0.9rem', padding: '6px 12px' }}>
                INS 編號: {additive.ins_or_e_number || '無'}
              </span>
              <button
                className="btn-ghost-sm"
                onClick={() => openSuggestModal('ins_or_e_number', 'INS編號', additive.ins_or_e_number)}
              >
                <Edit3 size={12} />
              </button>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span className="tag tag-amber" style={{ fontSize: '0.9rem', padding: '6px 12px' }}>
                每日容許量 (ADI): {additive.adi || '無限制/未知'}
              </span>
              <button
                className="btn-ghost-sm"
                onClick={() => openSuggestModal('adi', '每日容許量 (ADI)', additive.adi)}
              >
                <Edit3 size={12} />
              </button>
            </div>
          </div>
        </div>

        {/* Description Section */}
        <div style={{ marginBottom: '32px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px', flexWrap: 'wrap', gap: '8px' }}>
            <h3 style={{ display: 'flex', alignItems: 'center', gap: '8px', margin: 0 }}>
              <Info size={18} style={{ color: 'var(--success)' }} />
              消費者風險描述與說明
            </h3>
            <button
              className="btn-ghost-sm"
              style={{ fontSize: '0.8rem', padding: '4px 10px' }}
              onClick={() => openSuggestModal('description', '風險描述與說明', additive.description)}
            >
              <Edit3 size={14} />
              建議修改描述
            </button>
          </div>
          <div className="panel" style={{ padding: '20px', lineHeight: '1.7', color: 'var(--text-primary)', whiteSpace: 'pre-wrap' }}>
            {additive.description || '目前暫無詳細說明描述。'}
          </div>
        </div>
        {/* 特定族群健康風險 (Group Risks) */}
        <div style={{ marginBottom: '32px' }}>
          <h3 style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
            <AlertTriangle size={18} style={{ color: 'var(--off-orange)' }} />
            特異族群風險評估 (Group Risks)
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            {additive.risks && additive.risks.length > 0 ? (
              additive.risks.map((risk, idx) => (
                <div 
                  key={idx} 
                  className={`panel ${risk.concern === 'avoid' || risk.concern === 'danger' ? 'panel-danger' : 'panel-warning'}`}
                  style={{
                    padding: '20px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '8px'
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
                    <span style={{ 
                      fontWeight: 'bold', 
                      fontSize: '1.05rem', 
                      color: risk.concern === 'avoid' || risk.concern === 'danger' ? 'var(--danger)' : 'var(--warning)' 
                    }}>
                      適用族群：{risk.group} ({risk.concern === 'avoid' || risk.concern === 'danger' ? '建議避免' : '注意防範'})
                    </span>
                    <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                      可信度：{risk.confidence === 'verified' ? '官方認證' : '學術文獻支援'}
                    </span>
                  </div>
                  <p style={{ fontSize: '0.95rem', lineHeight: '1.6', color: 'var(--text-primary)' }}>
                    {risk.ai_reasoning || risk.reason}
                  </p>
                  {risk.source_title && (
                    <div style={{ fontSize: '0.85rem', marginTop: '6px' }}>
                      <span style={{ color: 'var(--text-muted)' }}>參考文獻：</span>
                      {risk.source_url ? (
                        <a 
                          href={risk.source_url} 
                          target="_blank" 
                          rel="noopener noreferrer" 
                          style={{ color: 'var(--off-orange-dark)', textDecoration: 'underline' }}
                        >
                          {risk.source_title} {risk.source_year ? `(${risk.source_year})` : ''}
                        </a>
                      ) : (
                        <span style={{ color: 'var(--text-secondary)' }}>
                          {risk.source_title} {risk.source_year ? `(${risk.source_year})` : ''}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              ))
            ) : (
              <div className="panel" style={{ padding: '20px', color: 'var(--text-secondary)' }}>
                暫無此添加物針對孕婦、兒童、慢性病等特異族群的危害評估警告。
              </div>
            )}
          </div>
        </div>

        {/* Allergen & Technical Purpose */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: '24px' }}>
          <div>
            <h3 style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
              <AlertTriangle size={18} style={{ color: additive.is_allergen ? 'var(--danger)' : 'var(--success)' }} />
              過敏原資訊
            </h3>
            <div className={additive.is_allergen ? 'panel panel-danger' : 'panel'} style={{ minHeight: '80px' }}>
              {additive.is_allergen ? (
                <div>
                  <p style={{ color: 'var(--danger)', fontWeight: '600', marginBottom: '6px' }}>此成分屬於過敏原</p>
                  <p style={{ fontSize: '0.9rem' }}>
                    {typeof additive.allergen_details === 'string'
                      ? additive.allergen_details
                      : JSON.stringify(additive.allergen_details) || '包含常見過敏源，敏感體質者請注意。'}
                  </p>
                </div>
              ) : (
                <p style={{ fontSize: '0.9rem', color: 'var(--text-secondary)' }}>非屬常見公告過敏原物種。</p>
              )}
            </div>
          </div>

          <div>
            <h3 style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
              <ShieldAlert size={18} style={{ color: 'var(--info)' }} />
              法規與用途
            </h3>
            <div className="panel" style={{ minHeight: '80px', fontSize: '0.9rem', lineHeight: '1.6' }}>
              <p style={{ marginBottom: '6px' }}><strong>國家法規狀態:</strong> {additive.regulatory_status_tw || '允許適量添加'}</p>
              <p style={{
                display: '-webkit-box',
                WebkitLineClamp: 3,
                WebkitBoxOrient: 'vertical',
                overflow: 'hidden'
              }}>
                <strong>使用範圍與限制:</strong> {additive.food_tech_purpose || '視加工實際需要適量使用。'}
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* Community Comments Section */}
      <div className="card" style={{ textAlign: 'left', marginBottom: '32px', padding: '32px' }}>
        <h2 style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '1.5rem', marginBottom: '24px' }}>
          <MessageSquare size={24} style={{ color: 'var(--off-orange)' }} />
          社群意見留言板
        </h2>

        {/* Comment Form */}
        <form onSubmit={handleCommentSubmit} style={{ marginBottom: '32px', borderBottom: '1px solid var(--border-color)', paddingBottom: '24px' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '16px', marginBottom: '16px' }}>
            <div className="form-group" style={{ margin: 0 }}>
              <label className="form-label">您的稱呼 / 暱稱</label>
              <input
                type="text"
                className="form-input"
                placeholder="例如：王小明"
                value={commentAuthor}
                onChange={(e) => setCommentAuthor(e.target.value)}
                required
              />
            </div>
            <div className="form-group" style={{ margin: 0 }}>
              <label className="form-label">留言內容</label>
              <textarea
                className="form-textarea"
                placeholder="分享您對此添加物的看法、使用經驗或學術文獻依據..."
                value={commentContent}
                onChange={(e) => setCommentContent(e.target.value)}
                required
              />
            </div>
          </div>
          <button type="submit" className="btn btn-primary" disabled={submittingComment}>
            <Send size={16} />
            {submittingComment ? '提交中...' : '提交留言'}
          </button>
        </form>

        {/* Comments List */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {additive.comments && additive.comments.length > 0 ? (
            additive.comments.map((comment) => (
              <div
                key={comment.id}
                className="panel"
                style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ fontWeight: '600', color: 'var(--off-orange-dark)' }}>{comment.author}</span>
                  <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                    {new Date(comment.created_at).toLocaleString('zh-TW')}
                  </span>
                </div>
                <p style={{ fontSize: '0.95rem', color: 'var(--text-primary)', whiteSpace: 'pre-wrap' }}>
                  {comment.content}
                </p>
              </div>
            ))
          ) : (
            <div className="empty-state" style={{ padding: '30px 20px' }}>
              目前尚無留言。歡迎成為第一個留言討論的人！
            </div>
          )}
        </div>
      </div>

      {/* Suggest Edit Modal */}
      {showSuggestModal && (
        <div className="modal-overlay">
          <div className="modal-content" style={{ textAlign: 'left' }}>
            <h2 style={{ fontSize: '1.4rem', marginBottom: '20px', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <Edit3 size={20} style={{ color: 'var(--off-orange)' }} />
              提出修改建議
            </h2>

            <div className="panel" style={{ padding: '12px 16px', marginBottom: '20px' }}>
              <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)', display: 'block', marginBottom: '4px' }}>修改欄位</span>
              <span style={{ fontWeight: '600', color: 'var(--off-orange-dark)' }}>{selectedFieldNameZh}</span>
            </div>

            <form onSubmit={handleSuggestSubmit}>
              <div className="form-group">
                <label className="form-label">建議新數值</label>
                {selectedField === 'description' ? (
                  <textarea
                    className="form-textarea"
                    value={suggestNewValue}
                    onChange={(e) => setSuggestNewValue(e.target.value)}
                    required
                  />
                ) : (
                  <input
                    type="text"
                    className="form-input"
                    value={suggestNewValue}
                    onChange={(e) => setSuggestNewValue(e.target.value)}
                    required
                  />
                )}
              </div>

              <div className="form-group">
                <label className="form-label">您的稱呼 / 來源佐證者</label>
                <input
                  type="text"
                  className="form-input"
                  placeholder="例如：林博士、社群協作者"
                  value={suggestName}
                  onChange={(e) => setSuggestName(e.target.value)}
                  required
                />
              </div>

              <div className="form-group">
                <label className="form-label">修改理由 / 文獻引用來源</label>
                <textarea
                  className="form-textarea"
                  placeholder="請說明修改原因，並提供科學依據或政府公告連結，以利審查..."
                  value={suggestReason}
                  onChange={(e) => setSuggestReason(e.target.value)}
                  required
                />
              </div>

              <div style={{ display: 'flex', gap: '12px', justifyContent: 'flex-end', marginTop: '24px' }}>
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => setShowSuggestModal(false)}
                  disabled={submittingSuggest}
                >
                  取消
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={submittingSuggest}
                >
                  {submittingSuggest ? '提交中...' : '提交建議'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
