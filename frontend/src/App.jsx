import { Navigate, Route, BrowserRouter as Router, Routes } from "react-router-dom";
import "./App.css";
import { useSupervisorStatus } from "./hooks/useSupervisorStatus";
import JamView from "./pages/JamView";
import Login from "./pages/Login";
import SetupWizard from "./pages/SetupWizard";
import SlotList from "./pages/SlotList";
import SlotProfile from "./pages/SlotProfile";
import Starting from "./pages/Starting";

/** Redirects "/" (and any unmatched non-jam path) based on supervisor
 * status -- setup/starting/crash_looping/running -> the right screen.
 * Jam mode routes are mounted separately below and never go through this,
 * matching the "fully separate top-level route" decision in the plan. */
function RootRedirect() {
  const { state } = useSupervisorStatus();
  if (state === null) return null; // brief flash while the first poll resolves
  if (state === "not_configured") return <Navigate to="/setup" replace />;
  if (state === "starting" || state === "crash_looping") return <Navigate to="/starting" replace />;
  return <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <Router>
      <Routes>
        <Route path="/jam/:jamToken" element={<JamView />} />
        <Route path="/setup" element={<SetupWizard />} />
        <Route path="/starting" element={<Starting />} />
        <Route path="/login" element={<Login />} />
        <Route path="/slots" element={<SlotList />} />
        <Route path="/slots/:name" element={<SlotProfile />} />
        <Route path="/" element={<RootRedirect />} />
        <Route path="*" element={<RootRedirect />} />
      </Routes>
    </Router>
  );
}
