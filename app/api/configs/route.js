import { jsonError, runWorker } from "@/src/lib/worker";

export async function GET() {
  try {
    return Response.json(await runWorker(["list-configs"]));
  } catch (error) {
    return jsonError(error);
  }
}

export async function POST(request) {
  try {
    const body = await request.json();
    return Response.json(
      await runWorker([
        "save-config-as",
        "--name",
        body.name || "",
        "--payload",
        JSON.stringify(body.config || {}),
      ])
    );
  } catch (error) {
    return jsonError(error);
  }
}
