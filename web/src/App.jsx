import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import SearchPortal from './components/SearchPortal';
import AdditiveDetail from './components/AdditiveDetail';
import AdminLogin from './components/AdminLogin';
import AdminDashboard from './components/AdminDashboard';

function App() {
  return (
    <Router>
      <Routes>
        <Route path="/" element={<SearchPortal />} />
        <Route path="/additive/:id" element={<AdditiveDetail />} />
        <Route path="/admin/login" element={<AdminLogin />} />
        <Route path="/admin/dashboard" element={<AdminDashboard />} />
      </Routes>
    </Router>
  );
}

export default App;
