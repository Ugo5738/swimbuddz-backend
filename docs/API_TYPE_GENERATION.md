# Schema Registry

SwimBuddz uses **contract-first development** with OpenAPI as the source of truth for API contracts between frontend and backend.

## How It Works

```
Backend Schemas (Pydantic)  →  OpenAPI JSON  →  TypeScript Types
      ↓                           ↓                    ↓
services/*/schemas.py       openapi.json        src/lib/api-types.ts
```

## Workflow

### Regenerating Types After Backend Changes

When you modify Pydantic schemas or add new endpoints:

```bash
# 1. Generate OpenAPI schema from FastAPI
cd swimbuddz-backend
python scripts/generate_openapi.py > openapi.json

# 2. Generate TypeScript types from OpenAPI
cd swimbuddz-frontend
npm run generate:types

# 3. Verify types compile
npx tsc --noEmit
```

Or use the workflow shortcut:

```
/generate-types
```

## Key Files

| File                                      | Purpose                            |
| ----------------------------------------- | ---------------------------------- |
| `swimbuddz-backend/openapi.json`          | Generated OpenAPI 3.0 spec         |
| `swimbuddz-frontend/src/lib/api-types.ts` | Generated TypeScript types         |
| `swimbuddz-frontend/src/lib/members.ts`   | Re-exports with API client methods |

## Best Practices

1. **Never hand-edit `api-types.ts`** - Always regenerate from backend
2. **Run `/generate-types` before committing** - Keeps types in sync
3. **Use schema re-exports** - Import from `members.ts`, `sessions.ts`, etc. for convenience
4. **Add JSDoc to Pydantic models** - They appear in generated types

## Example: Adding a New Field

```python
# 1. Add to Pydantic schema (services/members_service/schemas.py)
class MemberResponse(BaseModel):
    nickname: Optional[str] = Field(None, description="Display name")
```

```bash
# 2. Regenerate types
/generate-types
```

```typescript
// 3. Use in frontend (type is now available)
const member: Member = await MembersApi.getMe();
console.log(member.nickname); // TypeScript knows this exists
```

## Breaking Change Detection

Before merging, compare the generated `openapi.json` diff:

- Removed fields = breaking change
- Renamed fields = breaking change
- New optional fields = safe
- Changed types = breaking change

Consider using [openapi-diff](https://github.com/OpenAPITools/openapi-diff) for automated CI checks.
