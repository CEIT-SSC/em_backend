import type { ItemDetail, Presentation, SoloCompetition, CompetitionTeamDetail } from '../default';

export enum ItemTypeChoiceEnum {
    PRESENTATION = 'presentation',
    SOLO_COMPETITION = 'solo_competition',
    PRODUCT = 'product',
}

export enum ItemTypeChoiceEnumValues {
    presentation = 'presentation',
    solo_competition = 'solo_competition',
    product = 'product',
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
    itemDetails?: ItemDetail;
    price?: null;
    /**
    * @format date-time
    */
    addedAt?: string;
    eventId?: null;
    status?: null;
    reservedOrderId?: null;
    reservedOrderItemId?: null;
}

export interface Cart {
    /**
    * @label ID
    */
    id?: number;
    user: number;
    /**
    * @label Applied Discount Code
    */
    appliedDiscountCode?: number | null;
    discountCode?: string | null;
    items?: null;
    subtotalAmount?: null;
    discountAmount?: null;
    totalAmount?: null;
    /**
    * @format date-time
    */
    createdAt?: string;
}

export interface DiscountCodeTiny {
    /**
    * @maxLength 50
    */
    code: string;
    percentage?: number | null;
    amount?: number | null;
    targetType?: null;
    targetId?: number;
}

export interface ItemDetail {
    itemType?: null;
    presentation?: Presentation;
    soloCompetition?: SoloCompetition;
    competitionTeam?: CompetitionTeamDetail;
}

export interface OrderItem {
    /**
    * @label ID
    */
    id?: number;
    itemDetails?: ItemDetail;
    /**
    * @label Item Description (at time of order)
    * @maxLength 255
    */
    description: string;
    /**
    * @label Price (at time of order)
    */
    price: number;
}

export interface OrderItemWithEvent {
    /**
    * @label ID
    */
    id?: number;
    /**
    * @label Item Description (at time of order)
    * @maxLength 255
    */
    description: string;
    /**
    * @label Price (at time of order)
    */
    price: number;
    /**
    * @label Item Type
    */
    contentType?: number | null;
    /**
    * @label Item ID
    * @maximum 9223372036854775807
    */
    objectId?: number | null;
    eventId?: null;
    itemType?: null;
    itemTitle?: null;
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
    items?: OrderItemWithEvent[];
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
    items?: OrderItem[];
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

export interface RegisteredThing {
    itemType: string;
    status: string | null;
    role?: string | null;
    itemDetails?: ItemDetail;
}

