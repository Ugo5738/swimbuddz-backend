"""Re-export shim for store schemas.

Original 834-line module was split into per-domain submodules (see
docs/CONVENTIONS.md §12). All existing
``from services.store_service.schemas.main import X`` imports keep working
because everything is re-exported here.
"""

from .bundle import (
    BundleCreate,
    BundleDetailResponse,
    BundleItemCreate,
    BundleItemResponse,
    BundleUpdate,
)
from .cart import (
    ApplyDiscountRequest,
    CartItemCreate,
    CartItemResponse,
    CartItemUpdate,
    CartResponse,
)
from .category import (
    CategoryBase,
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
    CategoryWithChildren,
)
from .checkout import (
    CheckoutStartRequest,
    CheckoutStartResponse,
    DeliveryAddress,
    PaymentInitRequest,
    PaymentInitResponse,
)
from .collection import (
    CollectionBase,
    CollectionCreate,
    CollectionResponse,
    CollectionUpdate,
    CollectionWithProducts,
)
from .inventory import (
    InventoryAdjustment,
    InventoryItemResponse,
    InventoryVariantInfo,
    InventoryVariantProduct,
    LowStockItem,
)
from .order import (
    OrderItemImageInfo,
    OrderItemProductInfo,
    OrderItemResponse,
    OrderItemVariantInfo,
    OrderListResponse,
    OrderResponse,
    OrderStatusUpdate,
    OrderUpdate,
)
from .payout import (
    SupplierPayoutCreate,
    SupplierPayoutListResponse,
    SupplierPayoutResponse,
    SupplierPayoutStatusUpdate,
)
from .pickup_location import (
    PickupLocationBase,
    PickupLocationCreate,
    PickupLocationResponse,
    PickupLocationUpdate,
)
from .product import (
    DefaultVariantResponse,
    ProductBase,
    ProductCreate,
    ProductDetail,
    ProductImageBase,
    ProductImageCreate,
    ProductImageResponse,
    ProductImageUpdate,
    ProductListResponse,
    ProductResponse,
    ProductUpdate,
    ProductVariantBase,
    ProductVariantCreate,
    ProductVariantResponse,
    ProductVariantUpdate,
    ProductVariantWithInventory,
    ProductVideoBase,
    ProductVideoCreate,
    ProductVideoResponse,
    PublicProductDetail,
    PublicProductVariantInfo,
)
from .store_credit import (
    MemberStoreCreditSummary,
    StoreCreditCreate,
    StoreCreditResponse,
)
from .supplier import (
    SupplierBase,
    SupplierCreate,
    SupplierListResponse,
    SupplierResponse,
    SupplierUpdate,
)

__all__ = [
    # bundle
    "BundleCreate",
    "BundleDetailResponse",
    "BundleItemCreate",
    "BundleItemResponse",
    "BundleUpdate",
    # cart
    "ApplyDiscountRequest",
    "CartItemCreate",
    "CartItemResponse",
    "CartItemUpdate",
    "CartResponse",
    # category
    "CategoryBase",
    "CategoryCreate",
    "CategoryResponse",
    "CategoryUpdate",
    "CategoryWithChildren",
    # checkout
    "CheckoutStartRequest",
    "CheckoutStartResponse",
    "DeliveryAddress",
    "PaymentInitRequest",
    "PaymentInitResponse",
    # collection
    "CollectionBase",
    "CollectionCreate",
    "CollectionResponse",
    "CollectionUpdate",
    "CollectionWithProducts",
    # inventory
    "InventoryAdjustment",
    "InventoryItemResponse",
    "InventoryVariantInfo",
    "InventoryVariantProduct",
    "LowStockItem",
    # order
    "OrderItemImageInfo",
    "OrderItemProductInfo",
    "OrderItemResponse",
    "OrderItemVariantInfo",
    "OrderListResponse",
    "OrderResponse",
    "OrderStatusUpdate",
    "OrderUpdate",
    # payout
    "SupplierPayoutCreate",
    "SupplierPayoutListResponse",
    "SupplierPayoutResponse",
    "SupplierPayoutStatusUpdate",
    # pickup_location
    "PickupLocationBase",
    "PickupLocationCreate",
    "PickupLocationResponse",
    "PickupLocationUpdate",
    # product
    "DefaultVariantResponse",
    "ProductBase",
    "ProductCreate",
    "ProductDetail",
    "ProductImageBase",
    "ProductImageCreate",
    "ProductImageResponse",
    "ProductImageUpdate",
    "ProductListResponse",
    "ProductResponse",
    "ProductUpdate",
    "ProductVariantBase",
    "ProductVariantCreate",
    "ProductVariantResponse",
    "ProductVariantUpdate",
    "ProductVariantWithInventory",
    "ProductVideoBase",
    "ProductVideoCreate",
    "ProductVideoResponse",
    "PublicProductDetail",
    "PublicProductVariantInfo",
    # store_credit
    "MemberStoreCreditSummary",
    "StoreCreditCreate",
    "StoreCreditResponse",
    # supplier
    "SupplierBase",
    "SupplierCreate",
    "SupplierListResponse",
    "SupplierResponse",
    "SupplierUpdate",
]
