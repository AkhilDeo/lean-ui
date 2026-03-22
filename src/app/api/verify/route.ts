import { NextRequest, NextResponse } from 'next/server';
import { handleVerifyPost } from '../../../lib/verify-route';

export async function POST(request: NextRequest) {
  const response = await handleVerifyPost(request);
  return NextResponse.json(response.body, { status: response.status });
}
