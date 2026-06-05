import { useEffect, useRef, useState } from "react";
import { jobLogUrl } from "../api.js";

// Streams a job's log via Server-Sent Events and auto-scrolls.
export default function LogConsole({ jobId }) {
  const [lines, setLines] = useState([]);
  const [done, setDone] = useState(null);
  const boxRef = useRef(null);

  useEffect(() => {
    setLines([]);
    setDone(null);
    if (jobId == null) return;

    const es = new EventSource(jobLogUrl(jobId));
    es.onmessage = (e) => setLines((prev) => [...prev, e.data]);
    es.addEventListener("done", (e) => {
      setDone(e.data);
      es.close();
    });
    es.addEventListener("error", () => {
      // Either the job is missing or the stream dropped; stop retrying.
      es.close();
    });
    return () => es.close();
  }, [jobId]);

  useEffect(() => {
    if (boxRef.current) boxRef.current.scrollTop = boxRef.current.scrollHeight;
  }, [lines]);

  if (jobId == null) {
    return <div className="console empty-console">select or start a job to see logs</div>;
  }

  return (
    <div className="console-wrap">
      <div className="console-head">
        <span>job #{jobId} log</span>
        {done && <span className={`pill status-${done}`}>{done}</span>}
        {!done && <span className="pill status-running">streaming…</span>}
      </div>
      <pre className="console" ref={boxRef}>
        {lines.join("\n")}
      </pre>
    </div>
  );
}
