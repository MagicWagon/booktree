import { jsonError, runWorker } from "@/src/lib/worker";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(request, { params }) {
  try {
    const { id } = await params;
    const body = await request.json();
    return Response.json(
      await runWorker(["accept-match", "--id", id, "--match-id", String(body.matchId)])
    );
  } catch (error) {
    return jsonError(error);
  }
}
