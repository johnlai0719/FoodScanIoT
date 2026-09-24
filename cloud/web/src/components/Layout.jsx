import { useNavigate, useLocation } from 'react-router-dom';
import { Leaf, Shield } from 'lucide-react';

export default function Layout({ children }) {
  const navigate = useNavigate();
  const location = useLocation();
  const isAdminPage = location.pathname.startsWith('/admin');

  return (
    <>
      <header className="site-header">
        <div className="container site-header-inner">
          <a
            className="brand"
            onClick={(e) => { e.preventDefault(); navigate('/'); }}
            href="/"
          >
            <span className="brand-mark">
              <Leaf size={20} />
            </span>
            <span>
              <div className="brand-title">食品添加物社群審閱系統</div>
              <div className="brand-subtitle">智慧風險評估與意見反饋平台</div>
            </span>
          </a>

          <div className="header-actions">
            {!isAdminPage && (
              <button className="btn btn-secondary" onClick={() => navigate('/admin/login')}>
                <Shield size={16} />
                管理員登入
              </button>
            )}
          </div>
        </div>
      </header>

      <main className="page-main">
        {children}
      </main>

      <footer className="site-footer">
        <div className="container site-footer-inner">
          <span>食品添加物社群審閱系統 — 共同為食安把關</span>
          <span>資料僅供參考，實際法規請以政府公告為準</span>
        </div>
      </footer>
    </>
  );
}
