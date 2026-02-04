import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { App } from "./App";
import { DecisionDetail } from "./pages/DecisionDetail";
import { QuestionDetail } from "./pages/QuestionDetail";
import { CallDetail } from "./pages/CallDetail";
import { ClientDetail } from "./pages/ClientDetail";
import { TaskDetail } from "./pages/TaskDetail";
import "./App.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<App />} />
        <Route path="/decisions/:id" element={<DecisionDetail />} />
        <Route path="/questions/:id" element={<QuestionDetail />} />
        <Route path="/calls/:id" element={<CallDetail />} />
        <Route path="/clients/:name" element={<ClientDetail />} />
        <Route path="/tasks/:id" element={<TaskDetail />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);
