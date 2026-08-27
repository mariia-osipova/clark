import express, { Request, Response } from "express";
import path from "path";
import fs from "fs";
import { GoogleGenAI } from "@google/genai";

const app = express();
const PORT = 3000;
const HOST = "0.0.0.0";

// CORS and body parser
app.use(express.json({ limit: "50mb" }));
app.use(express.raw({ type: ["audio/*", "image/*"], limit: "50mb" }));

app.use((req, res, next) => {
  res.header("Access-Control-Allow-Origin", "*");
  res.header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS");
  res.header("Access-Control-Allow-Headers", "Content-Type, X-Session-Token");
  if (req.method === "OPTIONS") {
    return res.sendStatus(204);
  }
  next();
});

// Standard response envelope
function envelope(data: any = null, error: string | null = null, requestId: string = "") {
  return {
    ok: error === null,
    data: data !== null ? data : {},
    error,
    request_id: requestId || `req-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
  };
}

// ── In-Memory & File Data Stores ──────────────────────────────────────────────

interface Product {
  id: string;
  name: string;
  brand: string;
  package_size: string;
  price: number;
  list_price?: number;
  discount_pct?: number;
  available_quantity: number;
  category: string;
  image_url: string;
}

interface CartItem {
  product_id: string;
  name?: string;
  brand?: string;
  package_size?: string;
  price: number;
  quantity: number;
  image_url?: string;
  tag?: string;
}

interface Order {
  id: string;
  session_id?: string;
  items: CartItem[];
  total: number;
  created_at: string;
}

interface PendingClarification {
  session_id: string;
  pending_request_id: string;
  question: string;
  options: { id: string; label: string; product: Product }[];
  created_at: string;
}

// Load catalog
let catalog: Product[] = [];
const catalogPath = path.join(process.cwd(), "data", "catalog_snapshot.json");
try {
  if (fs.existsSync(catalogPath)) {
    catalog = JSON.parse(fs.readFileSync(catalogPath, "utf-8"));
  }
} catch (e) {
  console.warn("Could not load catalog_snapshot.json, using default seed");
}

if (!catalog || catalog.length === 0) {
  catalog = [
    {
      id: "720719",
      name: "Leche La Serenísima clásica 3% 1L",
      brand: "La Serenísima",
      package_size: "1L",
      price: 2570.0,
      available_quantity: 50,
      category: "Lácteos",
      image_url: "https://carrefourar.vteximg.com.br/arquivos/ids/636141/7790742363008_01.jpg.jpg?v=638780812788100000",
    },
    {
      id: "720720",
      name: "Leche La Serenísima descremada 1% 1L",
      brand: "La Serenísima",
      package_size: "1L",
      price: 2570.0,
      available_quantity: 45,
      category: "Lácteos",
      image_url: "https://carrefourar.vteximg.com.br/arquivos/ids/636141/7790742363008_01.jpg.jpg?v=638780812788100000",
    },
    {
      id: "380313",
      name: "Huevos blancos El Mercado 6 uni",
      brand: "El Mercado",
      package_size: "6 un",
      price: 1922.8,
      available_quantity: 80,
      category: "Huevos y Frescos",
      image_url: "https://carrefourar.vteximg.com.br/arquivos/ids/834041/7798108344487_02.jpg?v=639102195261730000",
    },
    {
      id: "758014",
      name: "Pan con Cereales y Semillas Fargo 400 grs",
      brand: "Fargo",
      package_size: "400 g",
      price: 5395.0,
      available_quantity: 40,
      category: "Panadería",
      image_url: "https://carrefourar.vteximg.com.br/arquivos/ids/687486/7793890261493_01.jpg?v=638900815850570000",
    },
    {
      id: "495133",
      name: "Manteca Classic calidad extra 200 g",
      brand: "Carrefour",
      package_size: "200 g",
      price: 2928.1,
      available_quantity: 70,
      category: "Lácteos",
      image_url: "https://carrefourar.vteximg.com.br/arquivos/ids/386972/7798152226326_E01.jpg?v=638852742884200000",
    },
    {
      id: "669429",
      name: "Yerba mate Playadito suave con palo 1 kg",
      brand: "Playadito",
      package_size: "1 kg",
      price: 4790.0,
      available_quantity: 90,
      category: "Almacén",
      image_url: "https://carrefourar.vteximg.com.br/arquivos/ids/207097/7793704000928_03.jpg?v=637623985987930000",
    },
    {
      id: "718787",
      name: "Arroz parboil Gallo oro en bolsa 1 kg",
      brand: "Gallo",
      package_size: "1 kg",
      price: 2500.0,
      available_quantity: 85,
      category: "Almacén",
      image_url: "https://carrefourar.vteximg.com.br/arquivos/ids/318039/7790070431417_02.jpg?v=638180311212270000",
    },
    {
      id: "726311",
      name: "Fideos tallarin N5 Lucchetti 500 g",
      brand: "Lucchetti",
      package_size: "500 g",
      price: 1499.0,
      available_quantity: 100,
      category: "Almacén",
      image_url: "https://carrefourar.vteximg.com.br/arquivos/ids/833465/7790070336118_02.jpg?v=639101356962770000",
    },
    {
      id: "699030",
      name: "Aceite de girasol Carrefour Classic alto omega pet 900 cc",
      brand: "Carrefour",
      package_size: "900 cc",
      price: 2998.6,
      available_quantity: 60,
      category: "Almacén",
      image_url: "https://carrefourar.vteximg.com.br/arquivos/ids/603859/7791720025291_01.jpg?v=638700617422870000",
    },
    {
      id: "736162",
      name: "Papel higienico doble hoja Higienol plus x4 30 mts",
      brand: "Higienol",
      package_size: "4 un",
      price: 4045.0,
      available_quantity: 50,
      category: "Limpieza",
      image_url: "https://carrefourar.vteximg.com.br/arquivos/ids/664417/7790250015536_01.jpg?v=638835174998930000",
    },
  ];
}

// In-Memory stores
const sessionCarts = new Map<string, { product_id: string; quantity: number }[]>();
const pendingClarifications = new Map<string, PendingClarification>();

// Seed initial orders
const orders: Order[] = [
  {
    id: "demo-order-1",
    session_id: "default",
    items: [
      { product_id: "720719", name: "Leche La Serenísima clásica 3% 1L", brand: "La Serenísima", package_size: "1L", price: 2570.0, quantity: 1, image_url: "https://carrefourar.vteximg.com.br/arquivos/ids/636141/7790742363008_01.jpg.jpg?v=638780812788100000" },
      { product_id: "758014", name: "Pan con Cereales y Semillas Fargo 400 grs", brand: "Fargo", package_size: "400 g", price: 5395.0, quantity: 1, image_url: "https://carrefourar.vteximg.com.br/arquivos/ids/687486/7793890261493_01.jpg?v=638900815850570000" },
      { product_id: "495133", name: "Manteca Classic calidad extra 200 g", brand: "Carrefour", package_size: "200 g", price: 2928.1, quantity: 1, image_url: "https://carrefourar.vteximg.com.br/arquivos/ids/386972/7798152226326_E01.jpg?v=638852742884200000" },
      { product_id: "736162", name: "Papel higienico doble hoja Higienol plus x4 30 mts", brand: "Higienol", package_size: "4 un", price: 4045.0, quantity: 1, image_url: "https://carrefourar.vteximg.com.br/arquivos/ids/664417/7790250015536_01.jpg?v=638835174998930000" },
    ],
    total: 14938.1,
    created_at: new Date(Date.now() - 14 * 24 * 60 * 60 * 1000).toISOString(),
  },
];

let preferences = {
  notes: "Compras para Jaime. Si hay lácteos, priorizá La Serenísima.",
  preferred_brands: { lácteos: "La Serenísima", panadería: "Fargo", yerba: "Playadito" },
  excluded_categories: [] as string[],
  household_size: 1,
};

let recurringPlan = {
  household_size: 1,
  monthly_budget: 25000.0,
  priority_items: ["720719", "380313", "758014", "669429"],
  preferred_brands: { lácteos: "La Serenísima", yerba: "Playadito" },
  strict_brand: false,
  excluded_categories: [] as string[],
  notes: "Canasta mensual de Jaime",
  updated_at: new Date().toISOString(),
};

// Profile presets
const cartProfiles: Record<string, { product_id: string; quantity: number }[]> = {
  desayuno: [
    { product_id: "720719", quantity: 1 },
    { product_id: "758014", quantity: 1 },
    { product_id: "495133", quantity: 1 },
    { product_id: "780104", quantity: 1 },
  ],
  despensa: [
    { product_id: "718787", quantity: 1 },
    { product_id: "726311", quantity: 2 },
    { product_id: "699030", quantity: 1 },
    { product_id: "669429", quantity: 1 },
    { product_id: "780110", quantity: 2 },
  ],
};

function hydrateCart(session_id: string): CartItem[] {
  const items = sessionCarts.get(session_id) || [];
  const catalogMap = new Map(catalog.map((p) => [p.id, p]));
  const result: CartItem[] = [];

  for (const item of items) {
    const p = catalogMap.get(item.product_id);
    if (p && p.available_quantity > 0) {
      result.push({
        product_id: p.id,
        name: p.name,
        brand: p.brand,
        package_size: p.package_size,
        price: p.price,
        quantity: item.quantity,
        image_url: p.image_url,
      });
    }
  }
  return result;
}

function findProductByQuery(query: string): Product | null {
  const cleanQ = query.toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "").trim();
  if (!cleanQ) return null;

  // Direct exact/substring match
  const exact = catalog.find((p) =>
    (p.name + " " + p.brand + " " + p.category)
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .includes(cleanQ)
  );
  if (exact) return exact;

  // Keyword token scoring
  const tokens = cleanQ.split(/\s+/);
  let bestScore = 0;
  let bestProd: Product | null = null;

  for (const p of catalog) {
    const text = (p.name + " " + p.brand + " " + p.category)
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "");
    let score = 0;
    for (const t of tokens) {
      if (t.length > 2 && text.includes(t)) {
        score += t.length;
      }
    }
    if (score > bestScore) {
      bestScore = score;
      bestProd = p;
    }
  }

  return bestScore >= 4 ? bestProd : null;
}

// ── API Routes ────────────────────────────────────────────────────────────────

// 1. GET /api/v1/catalog
app.get("/api/v1/catalog", (req: Request, res: Response) => {
  res.json(envelope({ products: catalog, total: catalog.length }));
});

// 2. GET /api/v1/cart
app.get("/api/v1/cart", (req: Request, res: Response) => {
  const session_id = (req.query.session_id as string) || (req.headers["x-session-token"] as string) || "default";
  const items = sessionCarts.get(session_id) || [];
  res.json(envelope({ session_id, items }));
});

// 3. POST /api/v1/cart
app.post("/api/v1/cart", (req: Request, res: Response) => {
  const { session_id, product_id, quantity } = req.body || {};
  const sid = session_id || (req.headers["x-session-token"] as string) || "default";
  if (!sid || !product_id) {
    return res.status(400).json(envelope(null, "session_id and product_id required"));
  }
  const qty = Math.max(1, parseInt(quantity, 10) || 1);
  const current = sessionCarts.get(sid) || [];
  const idx = current.findIndex((i) => i.product_id === product_id);
  if (idx >= 0) {
    current[idx].quantity = qty;
  } else {
    current.push({ product_id, quantity: qty });
  }
  sessionCarts.set(sid, current);
  res.json(envelope({ session_id: sid, product_id, quantity: qty }));
});

// 4. POST /api/v1/cart/remove
app.post("/api/v1/cart/remove", (req: Request, res: Response) => {
  const { session_id, product_id } = req.body || {};
  const sid = session_id || (req.headers["x-session-token"] as string) || "default";
  if (!sid || !product_id) {
    return res.status(400).json(envelope(null, "session_id and product_id required"));
  }
  const current = sessionCarts.get(sid) || [];
  const filtered = current.filter((i) => i.product_id !== product_id);
  sessionCarts.set(sid, filtered);
  res.json(envelope({ removed: true }));
});

// 5. POST /api/v1/cart/sync
app.post("/api/v1/cart/sync", (req: Request, res: Response) => {
  const sid = req.body?.session_id || (req.headers["x-session-token"] as string) || "default";
  const items = Array.isArray(req.body?.items) ? req.body.items : [];
  const normalized = items
    .filter((i: any) => i && i.product_id)
    .map((i: any) => ({
      product_id: String(i.product_id),
      quantity: Math.max(1, parseInt(i.quantity, 10) || 1),
    }));
  sessionCarts.set(sid, normalized);
  res.json(envelope({ synced: normalized.length }));
});

// 6. GET /api/v1/orders
app.get("/api/v1/orders", (req: Request, res: Response) => {
  res.json(envelope({ orders }));
});

// 7. POST /api/v1/orders
app.post("/api/v1/orders", (req: Request, res: Response) => {
  const sid = req.body?.session_id || (req.headers["x-session-token"] as string) || "default";
  const cartItems = hydrateCart(sid);
  if (cartItems.length === 0) {
    return res.status(400).json(envelope(null, "session cart is empty"));
  }
  const total = Number(cartItems.reduce((sum, item) => sum + item.price * item.quantity, 0).toFixed(2));
  const orderId = `ord-${Date.now().toString(36).toUpperCase()}`;
  const newOrder: Order = {
    id: orderId,
    session_id: sid,
    items: cartItems,
    total,
    created_at: new Date().toISOString(),
  };
  orders.unshift(newOrder);
  sessionCarts.set(sid, []); // clear cart
  res.json(envelope({ order_id: orderId, total }));
});

// 8. GET & PUT /api/v1/preferences
app.get("/api/v1/preferences", (req: Request, res: Response) => {
  res.json(envelope({ preferences, updated_at: new Date().toISOString() }));
});

const handlePrefUpdate = (req: Request, res: Response) => {
  const newPrefs = req.body?.preferences;
  if (newPrefs && typeof newPrefs === "object") {
    preferences = { ...preferences, ...newPrefs };
  }
  res.json(envelope({ preferences }));
};
app.put("/api/v1/preferences", handlePrefUpdate);
app.post("/api/v1/preferences", handlePrefUpdate);

// 9. GET & POST /api/v1/recurring-plan
app.get("/api/v1/recurring-plan", (req: Request, res: Response) => {
  res.json(envelope({ plan: recurringPlan }));
});

const handlePlanUpdate = (req: Request, res: Response) => {
  const newPlan = req.body?.plan;
  if (newPlan && typeof newPlan === "object") {
    recurringPlan = { ...recurringPlan, ...newPlan, updated_at: new Date().toISOString() };
  }
  res.json(envelope({ plan: recurringPlan }));
};
app.put("/api/v1/recurring-plan", handlePlanUpdate);
app.post("/api/v1/recurring-plan", handlePlanUpdate);

// 10. POST /api/v1/recurring-plan/generate
app.post("/api/v1/recurring-plan/generate", (req: Request, res: Response) => {
  const catalogMap = new Map(catalog.map((p) => [p.id, p]));
  const proposed_cart: CartItem[] = [];

  // Add priority items
  for (const pid of recurringPlan.priority_items) {
    const prod = catalogMap.get(pid);
    if (prod) {
      proposed_cart.push({
        product_id: prod.id,
        name: prod.name,
        brand: prod.brand,
        package_size: prod.package_size,
        price: prod.price,
        quantity: 1,
        image_url: prod.image_url,
        tag: "must_have",
      });
    }
  }

  // Add items from recent order history
  for (const order of orders.slice(0, 2)) {
    for (const item of order.items) {
      if (!proposed_cart.some((p) => p.product_id === item.product_id)) {
        const prod = catalogMap.get(item.product_id);
        if (prod) {
          proposed_cart.push({
            product_id: prod.id,
            name: prod.name,
            brand: prod.brand,
            package_size: prod.package_size,
            price: prod.price,
            quantity: item.quantity || 1,
            image_url: prod.image_url,
            tag: "recurring",
          });
        }
      }
    }
  }

  const total = Number(proposed_cart.reduce((sum, item) => sum + item.price * item.quantity, 0).toFixed(2));
  const budget_exceeded = recurringPlan.monthly_budget ? total > recurringPlan.monthly_budget : false;

  res.json(envelope({ proposed_cart, total, budget_exceeded }));
});

// 11. POST /api/v1/recurring-plan/accept
app.post("/api/v1/recurring-plan/accept", (req: Request, res: Response) => {
  const proposed_cart: CartItem[] = req.body?.proposed_cart || [];
  if (!Array.isArray(proposed_cart) || proposed_cart.length === 0) {
    return res.status(400).json(envelope(null, "proposed_cart must be a non-empty array"));
  }
  const total = Number(proposed_cart.reduce((sum, item) => sum + item.price * (item.quantity || 1), 0).toFixed(2));
  const orderId = `ord-plan-${Date.now().toString(36).toUpperCase()}`;
  orders.unshift({
    id: orderId,
    session_id: "default",
    items: proposed_cart,
    total,
    created_at: new Date().toISOString(),
  });
  res.json(envelope({ order_id: orderId, total, items: proposed_cart.length }));
});

// 12. Transcribe & Image Description
app.post("/api/v1/transcribe", (req: Request, res: Response) => {
  // Voice transcription fallback / mock extractor
  res.json(envelope({ text: "Necesito comprar 2 leches La Serenísima, 1 pan Fargo y 6 huevos" }));
});

app.post("/api/v1/describe-image", (req: Request, res: Response) => {
  // Vision extraction
  res.json(envelope({ text: "1 leche descremada, 1 pan lactal, manteca y yerba mate" }));
});

// 13. Auth stubs
app.post("/api/v1/auth/register", (req: Request, res: Response) => {
  res.status(501).json(envelope(null, "Not implemented"));
});
app.post("/api/v1/auth/login", (req: Request, res: Response) => {
  res.status(501).json(envelope(null, "Not implemented"));
});

// 14. POST /api/v1/chat — The Core Conversational Agent
app.post("/api/v1/chat", async (req: Request, res: Response) => {
  const sid = req.body?.session_id || (req.headers["x-session-token"] as string) || "default";
  const message: string = (req.body?.message || "").trim();
  const action: string = (req.body?.action || "").trim();
  const clarification_response = req.body?.clarification_response;
  const rawCart = Array.isArray(req.body?.cart) ? req.body.cart : [];

  let currentItems = sessionCarts.get(sid) || [];
  if (currentItems.length === 0 && rawCart.length > 0) {
    currentItems = rawCart.map((c: any) => ({
      product_id: c.product_id,
      quantity: c.quantity || 1,
    }));
    sessionCarts.set(sid, currentItems);
  }

  // Handle Clarification Response
  if (clarification_response && clarification_response.pending_request_id) {
    const pending = pendingClarifications.get(sid);
    const chosenOptionId = clarification_response.chosen_option_id;
    if (pending && pending.pending_request_id === clarification_response.pending_request_id) {
      const chosen = pending.options.find((o) => o.id === chosenOptionId) || pending.options[0];
      if (chosen && chosen.product) {
        const prod = chosen.product;
        const exists = currentItems.find((i) => i.product_id === prod.id);
        if (exists) {
          exists.quantity += 1;
        } else {
          currentItems.push({ product_id: prod.id, quantity: 1 });
        }
        sessionCarts.set(sid, currentItems);
        pendingClarifications.delete(sid);
        const hydrated = hydrateCart(sid);
        return res.json(
          envelope({
            reply: `¡Perfecto! Agregué ${prod.name} a tu carrito.`,
            cart: hydrated,
            clarification: null,
            missing_items: [],
            dropped_items: [],
          })
        );
      }
    }
  }

  // Action: Monthly basket generation shortcut
  if (action === "generate_monthly_basket" || message.toLowerCase().includes("canasta mensual")) {
    const catalogMap = new Map(catalog.map((p) => [p.id, p]));
    const proposed: CartItem[] = [];
    for (const pid of recurringPlan.priority_items) {
      const prod = catalogMap.get(pid);
      if (prod) {
        proposed.push({
          product_id: prod.id,
          name: prod.name,
          brand: prod.brand,
          package_size: prod.package_size,
          price: prod.price,
          quantity: 1,
          image_url: prod.image_url,
          tag: "must_have",
        });
      }
    }
    for (const order of orders.slice(0, 2)) {
      for (const item of order.items) {
        if (!proposed.some((p) => p.product_id === item.product_id)) {
          const prod = catalogMap.get(item.product_id);
          if (prod) {
            proposed.push({
              product_id: prod.id,
              name: prod.name,
              brand: prod.brand,
              package_size: prod.package_size,
              price: prod.price,
              quantity: item.quantity || 1,
              image_url: prod.image_url,
              tag: "recurring",
            });
          }
        }
      }
    }
    // Update session cart with proposed
    sessionCarts.set(
      sid,
      proposed.map((p) => ({ product_id: p.product_id, quantity: p.quantity }))
    );
    const hydrated = hydrateCart(sid);
    const total = hydrated.reduce((s, i) => s + i.price * i.quantity, 0);

    return res.json(
      envelope({
        reply: `Generé tu canasta mensual habitual con ${hydrated.length} productos clave (total: $${total.toFixed(2)}). Podés revisarla o confirmarla cuando quieras.`,
        cart: hydrated,
        proposed_cart: hydrated,
        clarification: null,
        missing_items: [],
        dropped_items: [],
      })
    );
  }

  // Profile presets: Desayuno / Despensa
  const lowerMsg = message.toLowerCase();
  if (lowerMsg.includes("desayuno") || lowerMsg.includes("cargar desayuno")) {
    const preset = cartProfiles["desayuno"];
    for (const item of preset) {
      const existing = currentItems.find((i) => i.product_id === item.product_id);
      if (existing) {
        existing.quantity += item.quantity;
      } else {
        currentItems.push({ ...item });
      }
    }
    sessionCarts.set(sid, currentItems);
    const hydrated = hydrateCart(sid);
    return res.json(
      envelope({
        reply: `Cargué tu perfil de desayuno: Leche La Serenísima, Pan con cereales Fargo, Manteca Carrefour y Dulce de leche.`,
        cart: hydrated,
        clarification: null,
        missing_items: [],
        dropped_items: [],
      })
    );
  }

  if (lowerMsg.includes("despensa") || lowerMsg.includes("cargar despensa")) {
    const preset = cartProfiles["despensa"];
    for (const item of preset) {
      const existing = currentItems.find((i) => i.product_id === item.product_id);
      if (existing) {
        existing.quantity += item.quantity;
      } else {
        currentItems.push({ ...item });
      }
    }
    sessionCarts.set(sid, currentItems);
    const hydrated = hydrateCart(sid);
    return res.json(
      envelope({
        reply: `Cargué tus básicos de despensa: Arroz Gallo oro, Fideos Lucchetti, Aceite girasol y Yerba Playadito.`,
        cart: hydrated,
        clarification: null,
        missing_items: [],
        dropped_items: [],
      })
    );
  }

  // Smart Ambiguity & Clarification Check
  // E.g. If user asks ambiguously for "leche" without specifying type or brand
  if (
    (lowerMsg === "leche" || lowerMsg === "quiero leche" || lowerMsg === "dame leche" || lowerMsg === "agregar leche") &&
    !lowerMsg.includes("entera") &&
    !lowerMsg.includes("descremada")
  ) {
    const lecheEntera = catalog.find((p) => p.id === "720719")!;
    const lecheDescremada = catalog.find((p) => p.id === "720720")!;
    const pendingReqId = `clarif-${Date.now()}`;
    const clarifData = {
      question: "¿Qué tipo de leche preferís?",
      options: [
        { id: "opt-entera", label: "Leche La Serenísima Clásica 3% (Entera)", product: lecheEntera },
        { id: "opt-descremada", label: "Leche La Serenísima 1% (Descremada)", product: lecheDescremada },
      ],
      pending_request_id: pendingReqId,
    };
    pendingClarifications.set(sid, {
      session_id: sid,
      ...clarifData,
      created_at: new Date().toISOString(),
    });

    return res.json(
      envelope({
        reply: "¿Qué tipo de leche preferís?",
        cart: null,
        clarification: clarifData,
        missing_items: [],
        dropped_items: [],
      })
    );
  }

  // Recipe / Multi-item parsing
  if (lowerMsg.includes("torta") || lowerMsg.includes("tiramisu") || lowerMsg.includes("merienda") || lowerMsg.includes("ingredientes")) {
    const addedProds: Product[] = [];
    const itemsToAdd = ["780104", "780105", "720719", "380313"]; // Dulce de leche, Chocolinas, Leche, Huevos
    for (const id of itemsToAdd) {
      const p = catalog.find((x) => x.id === id);
      if (p) {
        addedProds.push(p);
        const existing = currentItems.find((i) => i.product_id === p.id);
        if (existing) existing.quantity += 1;
        else currentItems.push({ product_id: p.id, quantity: 1 });
      }
    }
    sessionCarts.set(sid, currentItems);
    const hydrated = hydrateCart(sid);
    return res.json(
      envelope({
        reply: `¡Excelente idea! Busqué los ingredientes en el catálogo y agregué: ${addedProds.map((p) => p.name).join(", ")}.`,
        cart: hydrated,
        clarification: null,
        missing_items: [],
        dropped_items: [],
      })
    );
  }

  // General product search & addition
  const matchedProduct = findProductByQuery(message);
  if (matchedProduct) {
    // Quantity parsing
    let qty = 1;
    const qtyMatch = message.match(/\b([1-9]\d*)\b/);
    if (qtyMatch) {
      qty = parseInt(qtyMatch[1], 10);
    } else if (lowerMsg.includes("dos")) qty = 2;
    else if (lowerMsg.includes("tres")) qty = 3;
    else if (lowerMsg.includes("cuatro")) qty = 4;

    const existing = currentItems.find((i) => i.product_id === matchedProduct.id);
    if (existing) {
      existing.quantity += qty;
    } else {
      currentItems.push({ product_id: matchedProduct.id, quantity: qty });
    }
    sessionCarts.set(sid, currentItems);
    const hydrated = hydrateCart(sid);
    return res.json(
      envelope({
        reply: `Listo, sumé ${qty > 1 ? `${qty} unidades de ` : ""}${matchedProduct.name} a tu carrito ($${(matchedProduct.price * qty).toFixed(2)}).`,
        cart: hydrated,
        clarification: null,
        missing_items: [],
        dropped_items: [],
      })
    );
  }

  // LLM generation with Gemini API if configured
  if (process.env.GEMINI_API_KEY) {
    try {
      const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });
      const prompt = `Sos Clark, un asistente de compras de supermercado para Carrefour Argentina.
El usuario dice: "${message}".
Catálogo de productos disponibles: ${JSON.stringify(catalog.map((p) => ({ id: p.id, name: p.name, price: p.price, brand: p.brand })))}.
Respondé de manera amable, concisa y en español rioplatense si encontraste algún producto o cómo podés ayudarlo a armar su carrito.`;

      const response = await ai.models.generateContent({
        model: "gemini-2.5-flash",
        contents: prompt,
      });

      const replyText = response.text || "Entendido. ¿Querés que busque algún producto específico en el catálogo?";
      const hydrated = hydrateCart(sid);
      return res.json(
        envelope({
          reply: replyText,
          cart: hydrated,
          clarification: null,
          missing_items: [],
          dropped_items: [],
        })
      );
    } catch (llmErr) {
      console.warn("Gemini LLM error:", llmErr);
    }
  }

  // Default conversational response
  const hydrated = hydrateCart(sid);
  res.json(
    envelope({
      reply: `Entendido. Podés pedirme productos puntuales (ej: "leche La Serenísima", "yerba Playadito"), recetas, o tocar los atajos para cargar tu canasta mensual o desayuno.`,
      cart: hydrated,
      clarification: null,
      missing_items: [],
      dropped_items: [],
    })
  );
});

// ── Static Frontend Files ─────────────────────────────────────────────────────

const frontendDir = path.join(process.cwd(), "frontend");
app.use(express.static(frontendDir));

app.get("/dashboard", (req: Request, res: Response) => {
  res.sendFile(path.join(frontendDir, "dashboard.html"));
});

app.get("/chat", (req: Request, res: Response) => {
  res.sendFile(path.join(frontendDir, "chat.html"));
});

app.get("/catalog", (req: Request, res: Response) => {
  res.sendFile(path.join(frontendDir, "catalog.html"));
});

app.get("*", (req: Request, res: Response) => {
  res.sendFile(path.join(frontendDir, "index.html"));
});

// ── Server Boot ───────────────────────────────────────────────────────────────

app.listen(PORT, HOST, () => {
  console.log(`Clark Server running on http://${HOST}:${PORT}`);
});
