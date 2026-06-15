import { jsonError, runWorker } from "@/src/lib/worker";

export async function POST(request) {
  try {
    const body = await request.json();
    return Response.json(await runWorker(["set-active-config", "--path", body.path || ""]));
  } catch (error) {
    return jsonError(error);
  }
}
