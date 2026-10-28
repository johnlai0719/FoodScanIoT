import { useEffect, useRef, useState } from 'react';
import {
  QueuedAnalysis, QueueJob, loadQueuedAnalyses, saveQueuedAnalysis,
  removeQueuedAnalysis, pollQueuedAnalysis, taskKey,
} from '../utils/queuedAnalysis';

export function useQueuedAnalysis(callbacks: {
  restored: () => void;
  completed: (task: QueuedAnalysis, job: QueueJob) => void;
  failed: (task: QueuedAnalysis, message: string) => void;
}) {
  const latest = useRef(callbacks);
  latest.current = callbacks;
  const [tasks, setTasks] = useState<QueuedAnalysis[]>([]);
  const [current, setCurrent] = useState<QueuedAnalysis | null>(null);
  const [notice, setNotice] = useState('');
  const [storageWarning, setStorageWarning] = useState('');
  const selection = useRef(0);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    let active = true;
    const version = selection.current;
    void loadQueuedAnalyses().then(saved => {
      if (!active) return;
      setTasks(saved);
      if (version === selection.current && saved.length) {
        setCurrent(saved[0]);
        latest.current.restored();
      }
    }).catch(() => { if (active) setStorageWarning('無法讀取手機任務紀錄，請保留 Fog 任務編號。'); });
    return () => { active = false; alive.current = false; };
  }, []);

  useEffect(() => {
    if (!current) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | undefined;
    const poll = async () => {
      controller = new AbortController();
      const timeout = setTimeout(() => controller?.abort(), 5000);
      let settled = false;
      try {
        const job = await pollQueuedAnalysis(current, controller.signal);
        if (!active) return;
        if (job.state === 'completed') {
          settled = true;
          // Retain the durable record until HomeScreen confirms the result was rendered.
          latest.current.completed(current, job);
        } else if (job.state === 'failed') {
          settled = true;
          setCurrent(null);
          latest.current.failed(current, '排隊任務未能完成，請確認照片後重新送出。');
        } else {
          setNotice(job.state === 'processing' ? '雲端正在處理，請稍候。' : '資料已保存在 Fog，等待依序補送。');
        }
      } catch (error: any) {
        if (!active) return;
        const code = error?.httpStatus;
        if (code >= 400 && code < 500 && code !== 408 && code !== 429) {
          settled = true;
          setCurrent(null);
          latest.current.failed(current, code === 404
            ? 'Fog 查無此任務，可能已更換資料庫或任務已移除。請確認 Fog 位址或重新送出。'
            : error.message);
        } else {
          setNotice('目前無法查詢 Fog，任務紀錄已保留，稍後自動重試。');
        }
      } finally {
        clearTimeout(timeout);
        if (active && !settled) timer = setTimeout(poll, 10000);
      }
    };
    setNotice(current.message);
    void poll();
    return () => { active = false; clearTimeout(timer); controller?.abort(); };
  }, [current]);

  const pause = () => { selection.current++; setCurrent(null); };
  const select = (task: QueuedAnalysis) => { selection.current++; setCurrent({ ...task }); };
  const remember = async (task: QueuedAnalysis) => {
    // Validate before attempting any request assembled from a server acknowledgement.
    if (!/^[a-f0-9]{32}$/.test(task.id)) throw new Error('Fog 回傳的任務編號格式異常。');
    const remembered = tasks.find(t => taskKey(t) === taskKey(task)) || task;
    select(remembered);
    setTasks(previous => previous.some(t => taskKey(t) === taskKey(task)) ? previous : [...previous, task]);
    try {
      const saved = await saveQueuedAnalysis(task);
      if (alive.current) { setTasks(saved); setStorageWarning(''); }
    } catch {
      if (alive.current) setStorageWarning('Fog 已暫存，但手機無法保存任務紀錄；請記下任務編號，重啟後不保證能恢復。');
    }
  };
  const dismiss = async (task: QueuedAnalysis) => {
    if (current && taskKey(current) === taskKey(task)) pause();
    try {
      const saved = await removeQueuedAnalysis(task);
      if (alive.current) { setTasks(saved); setStorageWarning(''); }
    } catch {
      if (alive.current) setStorageWarning('手機任務紀錄移除失敗，請稍後再試。');
    }
  };
  return { tasks, current, notice, storageWarning, remember, select, pause, dismiss };
}
