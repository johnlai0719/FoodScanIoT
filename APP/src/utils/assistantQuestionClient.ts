export type AdditiveCitation = { name: string; ins: string; summary: string };

export type AssistantAnswer = {
  answer: string;
  additive?: AdditiveCitation;
  source?: { title: string; url: string };
};

export async function sendAssistantQuestion(question: string): Promise<AssistantAnswer> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 22000);
  try {
    const response = await fetch('http://127.0.0.1:8092/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
      signal: controller.signal,
    });
    if (!response.ok) throw new Error('Local additive assistant unavailable');
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
}
