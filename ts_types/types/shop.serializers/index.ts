import type { Presentation, ItemDetail, CompetitionTeamDetail, SoloCompetition } from '../default';

export enum ItemTypeChoiceEnum {
    PRESENTATION = 'presentation',
    SOLO_COMPETITION = 'solo_competition',
    COMPETITION_TEAM = 'competition_team',
}

export enum ItemTypeChoiceEnumValues {
    presentation = 'presentation',
    solo_competition = 'solo_competition',
    competition_team = 'competition_team',
}

export enum StatusChoiceEnum {
    PENDING_PAYMENT = 'pending_payment',
    AWAITING_GATEWAY_REDIRECT = 'awaiting_gateway_redirect',
    PAYMENT_FAILED = 'payment_failed',
    PROCESSING_ENROLLMENT = 'processing_enrollment',
    COMPLETED = 'completed',
    CANCELLED = 'cancelled',
    REFUND_PENDING = 'refund_pending',
    REFUNDED = 'refunded',
}

export enum StatusChoiceEnumValues {
    pending_payment = 'Pending Payment',
    awaiting_gateway_redirect = 'Awaiting Gateway Redirect',
    payment_failed = 'Payment Failed',
    processing_enrollment = 'Processing Enrollment/Registration',
    completed = 'Completed',
    cancelled = 'Cancelled',
    refund_pending = 'Refund Pending',
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
}

export interface Cart {
    /**
    * @label ID
    */
    id?: number;
    user?: number;
    items?: CartItem[];
    /**
    * @label Applied Discount Code
    */
    appliedDiscountCode?: number | null;
    appliedDiscountCodeDetails?: any;
    subtotal?: number;
    discountApplied?: number;
    total?: number;
    /**
    * @format date-time
    */
    createdAt?: string;
}

export interface ErrorResponse {
    error: string;
}

export interface ItemDetail {
    presentation?: Presentation;
    soloCompetition?: SoloCompetition;
    competitionTeam?: CompetitionTeamDetail;
    itemType: ItemTypeChoiceEnum;
}

export interface MessageResponse {
    message: string;
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

export interface OrderList {
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
    * @label Payment Gateway Authority (Zarinpal)
    */
    paymentGatewayAuthority?: string | null;
    /**
    * @label Payment Gateway Transaction ID (Zarinpal ref_id)
    */
    paymentGatewayTxnId?: string | null;
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

export interface PaymentInitiateResponse {
    /**
    * @format url
    */
    paymentUrl: string;
    authority: string;
}

