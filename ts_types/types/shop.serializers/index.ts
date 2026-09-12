import type { Presentation, SoloCompetition, Order } from '../default';

export enum ItemTypeChoiceEnum {
    PRESENTATION = 'presentation',
    SOLO_COMPETITION = 'solo_competition',
    PRODUCT = 'product',
    PACK = 'pack',
}

export enum ItemTypeChoiceEnumValues {
    presentation = 'presentation',
    solo_competition = 'solo_competition',
    product = 'product',
    pack = 'pack',
}

export enum StatusChoiceEnum {
    PENDING_PAYMENT = 'pending_payment',
    PROCESSING_ENROLLMENT = 'processing_enrollment',
    COMPLETED = 'completed',
    CANCELLED = 'cancelled',
    PAYMENT_FAILED = 'payment_failed',
    REFUNDED = 'refunded',
}

export enum StatusChoiceEnumValues {
    pending_payment = 'Pending Payment',
    processing_enrollment = 'Processing Enrollment/Registration',
    completed = 'Completed',
    cancelled = 'Cancelled',
    payment_failed = 'Payment Failed',
    refunded = 'Refunded',
}


export interface AddToCart {
    itemType: ItemTypeChoiceEnum;
    itemId: number;
}

export interface ApplyDiscount {
    /**
    * @maxLength 50
    */
    code: string;
}

export interface CartItem {
    /**
    * @label ID
    */
    id?: number;
    /**
    * @label Item Type
    */
    contentType: number;
    /**
    * @label Item ID
    * @maximum 9223372036854775807
    */
    objectId: number;
    price?: null;
    /**
    * @format date-time
    */
    addedAt?: string;
    eventId?: null;
}

export interface Cart {
    /**
    * @label Applied Discount Code
    */
    appliedDiscountCode?: number | null;
    discountCode?: string | null;
    presentations?: null;
    soloCompetitions?: null;
    products?: null;
    packs?: null;
    subtotalAmount?: null;
    discountAmount?: null;
    totalAmount?: null;
    /**
    * @format date-time
    */
    createdAt?: string;
}

export interface OrderCheckoutResult {
    order?: Order | null;
    paymentRequired: boolean;
    /**
    * @format url
    */
    paymentUrl: string | null;
    /**
    * @format uuid
    */
    topupId: string | null;
    walletBalance: number;
}

export interface OrderList {
    /**
    * @label ID
    */
    id?: number;
    /**
    * @label Order ID
    * @format uuid
    */
    orderId?: string;
    /**
    * @label Total Amount
    */
    totalAmount: number;
    /**
    * @label Order Status
    */
    status?: StatusChoiceEnum;
    /**
    * @format date-time
    */
    createdAt?: string;
    /**
    * @label Paid At
    * @format date-time
    */
    paidAt?: string | null;
    event?: number | null;
}

export interface Order {
    /**
    * @label Order ID
    * @format uuid
    */
    orderId?: string;
    user?: number | null;
    /**
    * @format email
    */
    userEmail?: string | null;
    event?: number | null;
    presentations?: null;
    soloCompetitions?: null;
    competitionTeams?: null;
    products?: null;
    packs?: null;
    /**
    * @label Subtotal Amount
    */
    subtotalAmount?: number;
    /**
    * @label Applied Discount Code
    */
    discountCodeApplied?: number | null;
    discountCodeStr?: string | null;
    /**
    * @label Discount Amount
    */
    discountAmount?: number;
    /**
    * @label Total Amount
    */
    totalAmount?: number;
    /**
    * @label Order Status
    */
    status?: StatusChoiceEnum;
    /**
    * @format date-time
    */
    createdAt?: string;
    /**
    * @label Paid At
    * @format date-time
    */
    paidAt?: string | null;
}

export interface Pack {
    /**
    * @label ID
    */
    id?: number;
    name?: string;
    description?: string;
    image?: File | null;
    event?: number | null;
    isActive?: boolean;
    calculatedPrice?: number;
    realPrice?: number;
    presentations?: null;
    soloCompetitions?: null;
    products?: null;
    /**
    * @format date-time
    */
    createdAt?: string;
}

export interface Product {
    /**
    * @label ID
    */
    id?: number;
    /**
    * @maxLength 255
    */
    name: string;
    description: string;
    price: number;
    image: File;
    features?: any | null;
    isActive?: boolean;
    /**
    * @format date-time
    */
    createdAt?: string;
    /**
    * @maximum 9223372036854775807
    */
    capacity?: number | null;
    event?: number | null;
}

export interface UserPurchases {
    presentations?: Presentation[];
    soloCompetitions?: SoloCompetition[];
    competitionTeams?: null;
    products?: Product[];
    packs?: Pack[];
}

