import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import AskScreen from "./screens/AskScreen";
import DashboardScreen from "./screens/DashboardScreen";
import LabScreen from "./screens/LabScreen";
import WhyScreen from "./screens/WhyScreen";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<AskScreen />} />
        <Route path="lab" element={<LabScreen />} />
        <Route path="why" element={<WhyScreen />} />
        <Route path="dashboard" element={<DashboardScreen />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
