import { NextResponse } from 'next/server';
import { handleRuntimesGet } from '../../../lib/verify-route';

export async function GET() {
  const response = await handleRuntimesGet();
  return NextResponse.json(response.body, { status: response.status });
}
