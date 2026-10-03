import { useState } from "react";
import { createRoot } from "react-dom/client";
import { Knowledge } from "./Knowledge";
import { Tasks } from "./Tasks";
import { Settings } from "./Settings";
import { Evaluation } from "./Evaluation";
import "./style.css";
function App() {
  const [page, setPage] = useState("Knowledge Base");
  return (
    <>
      <header>
        <h1>Scientific RAGAgent</h1>
        <p>可追溯的文献知识库与研究协作</p>
        <nav>
          {["Knowledge Base", "RAG", "Research", "Settings", "Evaluation"].map(
            (p) => (
              <button
                key={p}
                aria-current={page === p ? "page" : undefined}
                onClick={() => setPage(p)}
              >
                {p}
              </button>
            ),
          )}
        </nav>
      </header>
      <main>
        {page === "Knowledge Base" ? (
          <Knowledge />
        ) : page === "Settings" ? (
          <Settings />
        ) : page === "Evaluation" ? (
          <Evaluation />
        ) : (
          <Tasks key={page} research={page === "Research"} />
        )}
      </main>
      <footer>Evidence first · 审核结果仍需研究人员判断</footer>
    </>
  );
}
createRoot(document.getElementById("root")!).render(<App />);
