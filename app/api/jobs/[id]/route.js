import { jsonError, runWorker } from "@/src/lib/worker";

export async function GET(_request, { params }) {
  try {
    const { id } = await params;
    return Response.json(await runWorker(["get-job", "--id", id]));
  } catch (error) {
    return jsonError(error);
  }
}
