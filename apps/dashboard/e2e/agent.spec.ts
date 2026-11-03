// The chat box and hold-to-talk, against a stand-in for Person A's agent.
import { expect, test } from '@playwright/test';
import { agentServer, dashboard, multipart } from './support.ts';

test('the chat box posts a ComplaintText, after the agent allows it', async ({ page }) => {
  const agent = await agentServer(true);
  try {
    await page.goto(dashboard('operator', agent.url));
    await page.getByLabel('Complaint to the agent').fill('  It is too cold by the window  ');
    await page.getByLabel('Complaint to the agent').press('Enter');
    await expect(page.getByText('Sent to the agent.')).toBeVisible();
    const [preflight, post] = agent.received;
    expect(preflight?.method).toBe('OPTIONS'); // the client header makes the browser ask first
    expect(preflight?.headers['access-control-request-headers']).toContain('x-spotcool-client');
    expect(post?.method).toBe('POST');
    expect(post?.path).toBe('/chat');
    expect(post?.headers['x-spotcool-client']).toBe('dashboard');
    expect(JSON.parse(post?.body.toString('utf8') ?? '')).toEqual({ text: 'It is too cold by the window', source: 'occupant' });
  } finally {
    await agent.close();
  }
});

test('hold-to-talk works from the keyboard and posts a 16 kHz mono 16-bit WAV', async ({ page }) => {
  const agent = await agentServer(true);
  try {
    await page.goto(dashboard('operator', agent.url));
    const talk = page.getByRole('button', { name: 'Hold to talk' });
    await talk.focus();
    await page.keyboard.down('Space');
    await expect(page.getByRole('button', { name: 'Recording…' })).toHaveAttribute('aria-pressed', 'true');
    await page.waitForTimeout(1200);
    await page.keyboard.up('Space');
    await expect(page.getByTestId('talk-status')).toHaveText('Voice message sent to the agent.');
    const posts = agent.received.filter((r) => r.method === 'POST');
    expect(posts.map((r) => r.path)).toEqual(['/voice']);
    const post = posts[0];
    const parts = multipart(post?.body ?? Buffer.alloc(0), post?.headers['content-type'] ?? '');
    const audio = parts.get('audio') ?? Buffer.alloc(0);
    expect(audio.toString('ascii', 0, 4)).toBe('RIFF');
    expect(audio.toString('ascii', 8, 12)).toBe('WAVE');
    expect(audio.readUInt16LE(20)).toBe(1); // PCM
    expect(audio.readUInt16LE(22)).toBe(1); // mono
    expect(audio.readUInt32LE(24)).toBe(16_000);
    expect(audio.readUInt16LE(34)).toBe(16);
    expect(audio.readUInt32LE(40)).toBeGreaterThan(16_000); // over half a second of samples
    expect(JSON.parse(parts.get('meta')?.toString('utf8') ?? '{}')).toMatchObject({ source: 'occupant' });
  } finally {
    await agent.close();
  }
});

test('an agent that does not allow this page receives no complaint and no audio', async ({ page }) => {
  const sink = await agentServer(false);
  try {
    await page.goto(dashboard('operator', sink.url));
    await page.getByLabel('Complaint to the agent').fill('It is too cold');
    await page.getByLabel('Complaint to the agent').press('Enter');
    await expect(page.getByText(/Not sent: could not reach the agent/)).toBeVisible();
    const talk = page.getByRole('button', { name: 'Hold to talk' });
    await talk.focus();
    await page.keyboard.down('Space');
    await page.waitForTimeout(800);
    await page.keyboard.up('Space');
    await expect(page.getByTestId('talk-status')).toHaveText(/Not sent: could not reach the agent/);
    expect(sink.received.length).toBeGreaterThan(0); // the browser asked
    expect(sink.received.map((r) => r.method)).toEqual(sink.received.map(() => 'OPTIONS'));
    expect(sink.received.every((r) => r.body.length === 0)).toBe(true);
  } finally {
    await sink.close();
  }
});
