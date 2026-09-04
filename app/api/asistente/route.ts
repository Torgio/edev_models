import { proxyAssistantRequest } from '@/lib/assistant-proxy';

export const dynamic = 'force-dynamic';

export async function POST(request: Request) {
  const development = process.env.NODE_ENV === 'development';
  const authUpstream = process.env.DASHBOARD_API_URL
    ?? (development ? process.env.NEXT_PUBLIC_DASHBOARD_API_URL : undefined)
    ?? '';
  const assistantUpstream = process.env.ASSISTANT_API_URL ?? authUpstream;
  return proxyAssistantRequest(request, { authUpstream, assistantUpstream, development });
}
