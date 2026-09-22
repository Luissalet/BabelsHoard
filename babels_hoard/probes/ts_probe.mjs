// TypeScript compiler API probe. Run as: node ts_probe.mjs <node_modules_dir> <package_name>
// Prints one JSON object to stdout. Never imports anything from the target
// project besides its declaration files (.d.ts) - no code executes.
import fs from "node:fs";
import path from "node:path";
import ts from "typescript";

function fail(message) {
  console.log(JSON.stringify({ error: message }));
  process.exit(0);
}

const [, , nodeModulesDir, pkgName] = process.argv;
if (!nodeModulesDir || !pkgName) {
  fail("usage: ts_probe.mjs <node_modules_dir> <package_name>");
}

const pkgDir = path.join(nodeModulesDir, pkgName);
const pkgJsonPath = path.join(pkgDir, "package.json");
if (!fs.existsSync(pkgJsonPath)) {
  fail(`package not found: ${pkgName}`);
}
const pkgJson = JSON.parse(fs.readFileSync(pkgJsonPath, "utf8"));
const version = pkgJson.version || "0.0.0";

function findTypesEntry() {
  for (const field of ["types", "typings"]) {
    if (typeof pkgJson[field] === "string") {
      const p = path.join(pkgDir, pkgJson[field]);
      if (fs.existsSync(p)) return p;
    }
  }
  const exp = pkgJson.exports;
  if (exp && typeof exp === "object") {
    const dot = exp["."] ?? exp;
    const candidates = [];
    const collect = (node) => {
      if (!node || typeof node !== "object") return;
      if (typeof node.types === "string") candidates.push(node.types);
      for (const key of ["import", "require", "default"]) {
        if (node[key]) collect(node[key]);
      }
    };
    collect(dot);
    for (const c of candidates) {
      const p = path.join(pkgDir, c);
      if (fs.existsSync(p)) return p;
    }
  }
  const idx = path.join(pkgDir, "index.d.ts");
  if (fs.existsSync(idx)) return idx;
  const scopeless = pkgName.replace(/^@[^/]+\//, "");
  const atTypesDir = pkgName.startsWith("@")
    ? path.join(nodeModulesDir, "@types", pkgName.slice(1).replace("/", "__"))
    : path.join(nodeModulesDir, "@types", scopeless);
  const atTypesIdx = path.join(atTypesDir, "index.d.ts");
  if (fs.existsSync(atTypesIdx)) return atTypesIdx;
  return null;
}

const entry = findTypesEntry();
if (!entry) {
  fail(`no type declarations found for ${pkgName} (no "types"/"typings" field, no index.d.ts, no @types package)`);
}

let program, checker, sourceFile;
try {
  program = ts.createProgram([entry], {
    allowJs: true,
    target: ts.ScriptTarget.ES2020,
    module: ts.ModuleKind.NodeNext,
    moduleResolution: ts.ModuleResolutionKind.NodeNext,
    skipLibCheck: true,
  });
  checker = program.getTypeChecker();
  sourceFile = program.getSourceFile(entry);
} catch (e) {
  fail(`typescript compiler failed: ${e && e.message}`);
}

if (!sourceFile) fail(`could not parse ${entry}`);

const moduleSymbol = checker.getSymbolAtLocation(sourceFile);
let exportSymbols = [];
try {
  exportSymbols = moduleSymbol ? checker.getExportsOfModule(moduleSymbol) : [];
} catch (e) {
  fail(`could not read exports: ${e && e.message}`);
}

function declKind(decl) {
  if (!decl) return "value";
  if (ts.isClassDeclaration(decl)) return "class";
  if (ts.isInterfaceDeclaration(decl)) return "interface";
  if (ts.isFunctionDeclaration(decl)) return "function";
  if (ts.isTypeAliasDeclaration(decl)) return "type";
  if (ts.isEnumDeclaration(decl)) return "enum";
  if (ts.isVariableDeclaration(decl)) return "value";
  if (ts.isModuleDeclaration(decl)) return "namespace";
  return "value";
}

function safeTypeString(type, node) {
  try {
    return checker.typeToString(type, node, ts.TypeFormatFlags.NoTruncation | ts.TypeFormatFlags.WriteArrowStyleSignature);
  } catch (e) {
    return "";
  }
}

function jsdocOf(sym) {
  try {
    return ts.displayPartsToString(sym.getDocumentationComment(checker)) || null;
  } catch (e) {
    return null;
  }
}

function isDeprecated(sym) {
  try {
    return (sym.getJsDocTags(checker) || []).some((t) => t.name === "deprecated");
  } catch (e) {
    return false;
  }
}

// Full detail (types, docs, members) for the first MAX_EXPORTS exports; past
// that only names and kinds, which is all a named-import check needs, so
// big icon/component libraries (lucide-react) stay fully checkable.
const MAX_EXPORTS = 500;
const MAX_NAMES = 50000;
const results = [];
for (const sym of exportSymbols.slice(0, MAX_EXPORTS)) {
  const decl = sym.declarations && sym.declarations[0];
  const kind = declKind(decl);
  const name = sym.getName();
  let signature = "";
  try {
    const type = checker.getTypeOfSymbolAtLocation(sym, decl || sourceFile);
    signature = safeTypeString(type, decl);
  } catch (e) {
    signature = "";
  }
  const filePath = decl ? decl.getSourceFile().fileName : entry;
  const line = decl ? decl.getSourceFile().getLineAndCharacterOfPosition(decl.getStart()).line + 1 : null;

  const members = [];
  if ((kind === "class" || kind === "interface") && decl) {
    try {
      const declaredType = checker.getDeclaredTypeOfSymbol(sym);
      const props = checker.getPropertiesOfType(declaredType).slice(0, 100);
      for (const p of props) {
        let psig = "";
        try {
          psig = safeTypeString(checker.getTypeOfSymbolAtLocation(p, decl), decl);
        } catch (e) {
          /* ignore */
        }
        members.push({
          name: p.getName(),
          signature: psig,
          jsdoc: jsdocOf(p),
          deprecated: isDeprecated(p),
        });
      }
    } catch (e) {
      /* leave members empty rather than guess */
    }
  }

  results.push({
    name,
    kind,
    signature,
    jsdoc: jsdocOf(sym),
    deprecated: isDeprecated(sym),
    file: filePath,
    line,
    members,
  });
}

const namesOnly = exportSymbols.slice(MAX_EXPORTS, MAX_NAMES).map((sym) => ({
  name: sym.getName(),
  kind: declKind(sym.declarations && sym.declarations[0]),
}));

console.log(
  JSON.stringify({
    name: pkgName,
    version,
    entry: path.relative(pkgDir, entry),
    exports: results,
    names_only: namesOnly,
    truncated: exportSymbols.length > MAX_NAMES,
  })
);
