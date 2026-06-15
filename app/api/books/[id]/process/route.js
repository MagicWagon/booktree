import { jsonError, runWorker } from "@/src/lib/worker";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(_request, { params }) {
  try {
    const { id } = await params;
    return Response.json(await runWorker(["process", "--id", id]));
  } catch (error) {
    return jsonError(error);
  }
}
