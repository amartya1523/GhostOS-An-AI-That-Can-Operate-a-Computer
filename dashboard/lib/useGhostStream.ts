"use client";

import { useEffect, useReducer, useState } from "react";

import { streamUrl } from "./api";
import { type GhostEvent, initialRun, reduce } from "./state";

/** Subscribes to the live event stream, reconnecting with backoff if the server restarts. */
export function useGhostStream() {
  const [run, dispatch] = useReducer(reduce, initialRun);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retry = 0;
    let closed = false;
    let timer: ReturnType<typeof setTimeout>;

    const connect = () => {
      ws = new WebSocket(streamUrl());
      ws.onopen = () => {
        retry = 0;
        setConnected(true);
      };
      ws.onmessage = (msg) => dispatch(JSON.parse(msg.data) as GhostEvent);
      ws.onclose = () => {
        setConnected(false);
        if (!closed) timer = setTimeout(connect, Math.min(5000, 400 * 2 ** retry++));
      };
    };
    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      ws?.close();
    };
  }, []);

  const focus = (runId: string) => dispatch({ type: "focus", run_id: runId });
  return { run, connected, focus };
}
