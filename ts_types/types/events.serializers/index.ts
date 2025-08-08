import type { SoloCompetition, TeamContent, MemberDetailSubmit, Presenter, TeamMembershipUserDetail, GroupCompetition, Presentation, TeamMembership, ContentComment } from '../default';

export enum StatusChoiceEnum {
    PENDING_ADMIN_VERIFICATION = 'pending_admin_verification',
    REJECTED_BY_ADMIN = 'rejected_by_admin',
    APPROVED_AWAITING_PAYMENT = 'approved_awaiting_payment',
    IN_CART = 'in_cart',
    AWAITING_PAYMENT_CONFIRMATION = 'awaiting_payment_confirmation',
    PAYMENT_FAILED = 'payment_failed',
    ACTIVE = 'active',
    CANCELLED = 'cancelled',
}

export enum StatusChoiceEnum {
    PENDING_PAYMENT = 'pending_payment',
    COMPLETED_OR_FREE = 'completed_or_free',
    PAYMENT_FAILED = 'payment_failed',
    CANCELLED = 'cancelled',
}

export enum StatusChoiceEnumValues {
    pending_admin_verification = 'Pending Admin Verification',
    rejected_by_admin = 'Rejected by Admin',
    approved_awaiting_payment = 'Approved - Awaiting Payment',
    in_cart = 'In Cart (Awaiting Checkout)',
    awaiting_payment_confirmation = 'Awaiting Payment Confirmation',
    payment_failed = 'Payment Failed',
    active = 'Active',
    cancelled = 'Cancelled',
}

export enum StatusChoiceEnumValues {
    pending_payment = 'Pending Payment',
    completed_or_free = 'Completed/Free',
    payment_failed = 'Payment Failed',
    cancelled = 'Cancelled',
}

export enum TypeChoiceEnum {
    TALK = 'talk',
    WORKSHOP = 'workshop',
}

export enum TypeChoiceEnumValues {
    talk = 'Talk',
    workshop = 'Workshop',
}


export interface CommentCreate {
    text: string;
}

export interface CommentList {
    parentContentId: number;
    parentContentLikesCount: number;
    comments: ContentComment[];
}

export interface CommentUpdate {
    text: string;
}

export interface CompetitionTeamDetail {
    /**
    * @label ID
    */
    id?: number;
    /**
    * @label Team Name
    */
    name?: string;
    leaderDetails?: TeamMembershipUserDetail;
    groupCompetitionTitle?: string;
    /**
    * @label Team Status
    */
    status?: StatusChoiceEnum;
    /**
    * @label Has Admin Approved?
    */
    isApprovedByAdmin?: boolean;
    /**
    * @label Admin Remarks
    */
    adminRemarks?: string | null;
    memberships?: TeamMembership[];
    contentSubmission?: TeamContent;
    /**
    * @format date-time
    */
    createdAt?: string;
}

export interface CompetitionTeamSubmit {
    /**
    * @maxLength 255
    */
    teamName: string;
    memberDetails: MemberDetailSubmit[];
}

export interface ContentComment {
    /**
    * @label ID
    */
    id?: number;
    user?: number;
    userDetails?: TeamMembershipUserDetail;
    /**
    * @label Commented Content
    */
    teamContent?: number;
    /**
    * @label Comment Text
    */
    text: string;
    /**
    * @format date-time
    */
    createdAt?: string;
}

export interface ContentImage {
    /**
    * @label ID
    */
    id?: number;
    image: File;
    /**
    * @maxLength 255
    */
    caption?: string | null;
    /**
    * @format date-time
    */
    uploadedAt?: string;
}

export interface ContentLike {
    /**
    * @label ID
    */
    id?: number;
    user?: number;
    /**
    * @format email
    */
    userEmail?: string;
    /**
    * @label Liked Content
    */
    teamContent?: number;
    /**
    * @format date-time
    */
    createdAt?: string;
}

export interface ErrorResponse {
    error: string;
}

export interface EventDetail {
    /**
    * @label ID
    */
    id?: number;
    /**
    * @label Event Title
    * @maxLength 255
    */
    title: string;
    /**
    * @label Event Description
    */
    description: string;
    /**
    * @label Start Date & Time
    * @format date-time
    */
    startDate: string;
    /**
    * @label End Date & Time
    * @format date-time
    */
    endDate: string;
    /**
    * @label Is Event Active?
    */
    isActive?: boolean;
    presentations?: Presentation[];
    soloCompetitions?: SoloCompetition[];
    groupCompetitions?: GroupCompetition[];
    /**
    * @label Event Poster
    */
    poster?: File | null;
    /**
    * @format date-time
    */
    createdAt?: string;
    /**
    * @label Page URL
    * @maxLength 500
    * @format url
    */
    landingUrl?: string | null;
}

export interface EventList {
    /**
    * @label ID
    */
    id?: number;
    /**
    * @label Event Title
    * @maxLength 255
    */
    title: string;
    /**
    * @label Start Date & Time
    * @format date-time
    */
    startDate: string;
    /**
    * @label End Date & Time
    * @format date-time
    */
    endDate: string;
    /**
    * @label Event Poster
    */
    poster?: File | null;
    /**
    * @label Is Event Active?
    */
    isActive?: boolean;
    /**
    * @label Page URL
    * @maxLength 500
    * @format url
    */
    landingUrl?: string | null;
}

export interface GroupCompetition {
    /**
    * @label ID
    */
    id?: number;
    /**
    * @label Parent Event
    */
    event: number;
    eventTitle?: string;
    /**
    * @label Competition Title
    * @maxLength 255
    */
    title: string;
    description: string;
    /**
    * @label Start Date & Time
    * @format date-time
    */
    startDatetime: string;
    /**
    * @label End Date & Time
    * @format date-time
    */
    endDatetime: string;
    rules?: string | null;
    /**
    * @label Is Paid?
    */
    isPaid?: boolean;
    /**
    * @label Price per Group (e.g., Toman)
    */
    pricePerGroup?: number | null;
    /**
    * @label Prize Details
    */
    prizeDetails?: string | null;
    /**
    * @label Is Active for Registration?
    */
    isActive?: boolean;
    /**
    * @label Competition Poster
    */
    poster?: File | null;
    /**
    * @label Min Group Size
    * @maximum 9223372036854775807
    */
    minGroupSize?: number;
    /**
    * @label Max Group Size
    * @maximum 9223372036854775807
    */
    maxGroupSize: number;
    /**
    * @label Max Teams
    * @maximum 9223372036854775807
    */
    maxTeams?: number | null;
    /**
    * @label Requires Admin Approval for Teams?
    */
    requiresAdminApproval?: boolean;
    /**
    * @label Member Verification Instructions
    */
    memberVerificationInstructions?: string | null;
    /**
    * @label Allow Teams to Submit Content?
    */
    allowContentSubmission?: boolean;
    /**
    * @format date-time
    */
    createdAt?: string;
}

export interface LikeStatus {
    liked: boolean;
    likesCount: number;
}

export interface MemberDetailSubmit {
    /**
    * @format email
    */
    email: string;
    governmentIdPicture?: File | null;
}

export interface MessageResponse {
    message: string;
}

export interface PostDetail {
    /**
    * @label ID
    */
    id?: number;
    title?: string;
    excerpt?: string;
    bodyMarkdown?: string;
    /**
    * @format date-time
    */
    publishedAt?: string;
}

export interface PostList {
    /**
    * @label ID
    */
    id?: number;
    title?: string;
    excerpt?: string;
    /**
    * @format date-time
    */
    publishedAt?: string;
}

export interface PresentationEnrollment {
    /**
    * @label ID
    */
    id?: number;
    /**
    * @format email
    */
    userEmail?: string;
    presentationTitle?: string;
    /**
    * @label Enrollment Status
    */
    status?: StatusChoiceEnum;
    /**
    * @format date-time
    */
    enrolledAt?: string;
    orderItem?: number | null;
}

export interface Presentation {
    /**
    * @label ID
    */
    id?: number;
    /**
    * @label Parent Event
    */
    event: number;
    eventTitle?: string;
    /**
    * @label Presentation Title
    * @maxLength 255
    */
    title: string;
    /**
    * @label Presentation Description
    */
    description: string;
    presentersDetails?: Presenter[];
    presenterIds?: number[];
    type?: TypeChoiceEnum;
    /**
    * @label Is Online?
    */
    isOnline?: boolean;
    /**
    * @label Location (if offline)
    * @maxLength 255
    */
    location?: string | null;
    /**
    * @label Online Link (if online)
    * @maxLength 200
    * @format url
    */
    onlineLink?: string | null;
    /**
    * @label Start Time
    * @format date-time
    */
    startTime: string;
    /**
    * @label End Time
    * @format date-time
    */
    endTime: string;
    /**
    * @label Is Paid?
    */
    isPaid?: boolean;
    /**
    * @label Price (e.g., Toman)
    */
    price?: number | null;
    /**
    * @maximum 9223372036854775807
    */
    capacity?: number | null;
    /**
    * @format date-time
    */
    createdAt?: string;
}

export interface Presenter {
    /**
    * @label ID
    */
    id?: number;
    /**
    * @label Full Name
    * @maxLength 255
    */
    name: string;
    /**
    * @label Public Contact Email
    * @maxLength 254
    * @format email
    */
    email?: string | null;
    /**
    * @label Biography
    */
    bio?: string | null;
    /**
    * @label Presenter Picture
    */
    presenterPicture?: File | null;
    /**
    * @format date-time
    */
    createdAt?: string;
}

export interface SoloCompetitionRegistration {
    /**
    * @label ID
    */
    id?: number;
    /**
    * @format email
    */
    userEmail?: string;
    soloCompetitionTitle?: string;
    /**
    * @label Registration Status
    */
    status?: StatusChoiceEnum;
    /**
    * @format date-time
    */
    registeredAt?: string;
    orderItem?: number | null;
}

export interface SoloCompetition {
    /**
    * @label ID
    */
    id?: number;
    /**
    * @label Parent Event
    */
    event: number;
    eventTitle?: string;
    /**
    * @label Competition Title
    * @maxLength 255
    */
    title: string;
    description: string;
    /**
    * @label Start Date & Time
    * @format date-time
    */
    startDatetime: string;
    /**
    * @label End Date & Time
    * @format date-time
    */
    endDatetime: string;
    /**
    * @label Competition Poster
    */
    poster?: File | null;
    rules?: string | null;
    /**
    * @label Is Paid?
    */
    isPaid?: boolean;
    /**
    * @label Price per Participant (e.g., Toman)
    */
    pricePerParticipant?: number | null;
    /**
    * @label Prize Details
    */
    prizeDetails?: string | null;
    /**
    * @label Is Active for Registration?
    */
    isActive?: boolean;
    /**
    * @label Max Participants
    * @maximum 9223372036854775807
    */
    maxParticipants?: number | null;
    /**
    * @format date-time
    */
    createdAt?: string;
}

export interface TeamContent {
    /**
    * @label ID
    */
    id?: number;
    team?: number;
    teamName?: string;
    /**
    * @label Content Description
    */
    description: string;
    /**
    * @label Link to External File/Repository
    * @maxLength 500
    * @format url
    */
    fileLink?: string | null;
    images?: ContentImage[];
    uploadedImages?: File[];
    likesCount?: null;
    commentsCount?: null;
    isLikedByRequester?: null;
    /**
    * @format date-time
    */
    createdAt?: string;
}

export interface TeamMembership {
    /**
    * @label ID
    */
    id?: number;
    userDetails?: TeamMembershipUserDetail;
    /**
    * @label Government ID Picture
    */
    governmentIdPicture?: File | null;
    /**
    * @format date-time
    */
    joinedAt?: string;
}

export interface TeamMembershipUserDetail {
    /**
    * @label ID
    */
    id?: number;
    /**
    * @label Email Address
    * @maxLength 254
    * @format email
    */
    email: string;
    /**
    * @label First Name
    * @maxLength 150
    */
    firstName?: string;
    /**
    * @label Last Name
    * @maxLength 150
    */
    lastName?: string;
    /**
    * @label Profile Picture
    */
    profilePicture?: File | null;
}

